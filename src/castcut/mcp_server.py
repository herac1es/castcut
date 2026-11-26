"""FastMCP stdio server exposing CastCut's deterministic video-editing tools to Antigravity and Codex."""

from __future__ import annotations

import json
from pathlib import Path

from castcut.asr import faster_whisper_available
from castcut.exporters import export_episode
from castcut.ingest import ingest_episode
from castcut.media import find_cjk_font, resolve_ffmpeg, resolve_ffprobe
from castcut.models import (
    AspectMode,
    ClipCategory,
    MixedOrientationPolicy,
    ResolutionTier,
    TemplateType,
)
from castcut.planner import build_fine_plan, build_rough_plan
from castcut.qc import run_qc
from castcut.renderer import render_episode
from castcut.workspace import (
    EpisodePaths,
    init_episode_workspace,
    load_clip_catalog,
    save_clip_catalog,
)


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer as ServerCls
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as ServerCls
        except ImportError as exc:
            raise RuntimeError(
                "MCP extra is not installed. Run `uv sync --extra mcp` to enable castcut-mcp."
            ) from exc

    mcp = ServerCls("castcut")

    @mcp.tool()
    def castcut_doctor() -> str:
        """Check local FFmpeg, FFprobe, CJK font, and ASR environment health."""
        ff = resolve_ffmpeg()
        fp = resolve_ffprobe()
        return json.dumps(
            {
                "ffmpeg": ff.__dict__,
                "ffprobe": fp.__dict__ if fp else None,
                "cjk_font": find_cjk_font(),
                "faster_whisper": faster_whisper_available(),
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.tool()
    def castcut_init_episode(
        episode_dir: str,
        title: str,
        aspect: str = "auto",
        resolution: str = "1080p",
        mixed_orientation: str = "blur_pillarbox",
        template: str = "fishing-vlog",
        location: str = "",
        bgm_path: str | None = None,
    ) -> str:
        """Initialize a new lure fishing vlog episode workspace."""
        cfg = init_episode_workspace(
            episode_dir=Path(episode_dir),
            title=title,
            aspect=AspectMode(aspect),
            resolution=ResolutionTier(resolution),
            mixed_orientation=MixedOrientationPolicy(mixed_orientation),
            template=TemplateType(template),
            location=location,
            bgm_path=bgm_path,
        )
        return cfg.model_dump_json(indent=2)

    @mcp.tool()
    def castcut_ingest(
        episode_dir: str,
        asr_mode: str = "auto",
        make_proxies: bool = True,
        make_contacts: bool = True,
    ) -> str:
        """Stage 1: Probe raw footage, analyze silence/peaks/scenes, transcribe, and generate proxies & contact sheets."""
        media_index, catalog = ingest_episode(
            episode_dir=Path(episode_dir),
            asr_mode=asr_mode,
            make_proxies=make_proxies,
            make_contacts=make_contacts,
        )
        return json.dumps(
            {
                "dominant_orientation": media_index.dominant_orientation,
                "total_duration": media_index.total_duration,
                "has_10bit_media": media_index.has_10bit_media,
                "media_count": len(media_index.files),
                "clip_count": len(catalog.clips),
                "proxies": [m.proxy_rel_path for m in media_index.files if m.proxy_rel_path],
                "contact_sheets": [
                    m.contact_sheet_rel_path for m in media_index.files if m.contact_sheet_rel_path
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.tool()
    def castcut_list_clips(episode_dir: str, only_kept: bool = False) -> str:
        """Return the triaged candidate clips JSON for review."""
        catalog = load_clip_catalog(EpisodePaths(Path(episode_dir)).clips_json)
        clips = [c.model_dump(mode="json") for c in catalog.clips if not only_kept or c.keep]
        return json.dumps(clips, ensure_ascii=False, indent=2)

    @mcp.tool()
    def castcut_update_clip(
        episode_dir: str,
        clip_id: str,
        category: str | None = None,
        score: float | None = None,
        keep: bool | None = None,
        start: float | None = None,
        end: float | None = None,
        agent_note: str | None = None,
    ) -> str:
        """Update category, score, keep flag, trim range, or note for one candidate clip."""
        paths = EpisodePaths(Path(episode_dir))
        catalog = load_clip_catalog(paths.clips_json)
        target = next((c for c in catalog.clips if c.clip_id == clip_id), None)
        if target is None:
            raise ValueError(f"Clip not found: {clip_id}")
        if category is not None:
            target.category = ClipCategory(category)
        if score is not None:
            target.score = round(max(0.0, min(1.0, score)), 2)
        if keep is not None:
            target.keep = keep
        if start is not None:
            target.start = round(max(0.0, start), 3)
        if end is not None:
            target.end = round(max(target.start + 0.2, end), 3)
        target.duration = round(max(0.0, target.end - target.start), 3)
        if agent_note is not None:
            target.agent_note = agent_note
        save_clip_catalog(paths.clips_json, catalog)
        return target.model_dump_json(indent=2)

    @mcp.tool()
    def castcut_build_plan(
        episode_dir: str,
        stage: str = "rough",
        template: str | None = None,
    ) -> str:
        """Build rough_cut.yaml (stage='rough') or fine_cut.yaml (stage='fine')."""
        ep_dir = Path(episode_dir)
        if stage == "rough":
            plan = build_rough_plan(ep_dir, template_override=TemplateType(template) if template else None)
        elif stage == "fine":
            plan = build_fine_plan(ep_dir)
        else:
            raise ValueError("stage must be 'rough' or 'fine'")
        return json.dumps(
            {
                "stage": plan.stage,
                "width": plan.width,
                "height": plan.height,
                "total_duration": plan.total_duration,
                "item_count": len(plan.items),
                "subtitle_count": len(plan.subtitles),
                "overlay_count": len(plan.overlays),
            },
            ensure_ascii=False,
            indent=2,
        )

    @mcp.tool()
    def castcut_render_preview(
        episode_dir: str,
        stage: str = "fine",
        burn_subtitles: bool | None = None,
    ) -> str:
        """Render rough_preview.mp4 or fine_preview.mp4."""
        out = render_episode(Path(episode_dir), stage=stage, burn_subtitles=burn_subtitles)
        return json.dumps({"rendered_path": str(out)}, ensure_ascii=False)

    @mcp.tool()
    def castcut_export_nle(
        episode_dir: str,
        formats: list[str] | None = None,
        stage: str = "fine",
    ) -> str:
        """Export zero-VIP NLE handoff package (otio, fcpxml, srt, optional jianying)."""
        res = export_episode(Path(episode_dir), formats=formats or ["otio", "fcpxml", "srt"], stage=stage)
        return json.dumps({k: str(v) for k, v in res.items()}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def castcut_run_qc(episode_dir: str, stage: str = "fine") -> str:
        """Run Stage 4 quality checks and generate exports/qc_contact.jpg."""
        report = run_qc(Path(episode_dir), stage=stage)
        return report.model_dump_json(indent=2)

    return mcp


def main() -> None:
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
