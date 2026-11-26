"""Zero-VIP NLE export orchestrator (OTIO, FCPXML, SRT, optional Jianying baseline draft)."""

from __future__ import annotations

from pathlib import Path

from castcut.exporters.fcpxml import export_fcpxml
from castcut.exporters.jianying import export_jianying_draft
from castcut.exporters.otio import export_otio
from castcut.exporters.srt import write_srt
from castcut.models import EditPlan, MediaFile, MediaIndex, MixedOrientationPolicy
from castcut.overlays import materialize_overlays
from castcut.planner import has_aspect_mismatch
from castcut.workspace import EpisodePaths, load_edit_plan, load_media_index, save_edit_plan


def _prepare_nle_assets(
    plan: EditPlan,
    media_index: MediaIndex,
    paths: EpisodePaths,
    bake_mixed: bool,
    bake_ducked_bgm: bool,
) -> tuple[EditPlan, MediaIndex]:
    """Return a derived export plan/index with pre-baked mixed-orientation clips & ducked BGM WAV."""
    from castcut.renderer import render_adapted_mixed_clip, render_ducked_bgm_wav

    export_plan = plan.model_copy(deep=True)
    export_index = media_index.model_copy(deep=True)
    media_map = {m.media_id: m for m in export_index.files}

    if bake_mixed:
        canvas_orient = "landscape" if export_plan.width >= export_plan.height else "portrait"
        adapted_dir = paths.exports_dir / "adapted"
        for item in export_plan.items:
            media = media_map.get(item.media_id)
            src_w = media.width if media else item.source_width
            src_h = media.height if media else item.source_height
            if (
                has_aspect_mismatch(src_w, src_h, export_plan.width, export_plan.height)
                and item.framing != MixedOrientationPolicy.FIT
            ):
                src = Path(item.source_abs_path)
                if not src.exists():
                    src = (paths.root / item.source_rel_path).resolve()
                if not src.exists():
                    continue
                baked_path = adapted_dir / f"{item.item_id}_{item.clip_id}_adapted.mp4"
                render_adapted_mixed_clip(
                    item=item,
                    source_path=src,
                    has_audio=media.has_audio if media else True,
                    width=export_plan.width,
                    height=export_plan.height,
                    fps=export_plan.fps,
                    dest=baked_path,
                )
                baked_dur = round(item.output_duration, 3)
                baked_id = f"{item.media_id}_{item.item_id}_ADAPTED"
                export_index.files.append(
                    MediaFile(
                        media_id=baked_id,
                        filename=baked_path.name,
                        rel_path=baked_path.relative_to(paths.root).as_posix(),
                        abs_path=str(baked_path.resolve()),
                        duration=baked_dur,
                        width=export_plan.width,
                        height=export_plan.height,
                        orientation=canvas_orient,
                        fps=export_plan.fps,
                        video_codec="h264",
                        audio_codec="aac",
                        has_audio=True,
                        pix_fmt="yuv420p",
                        is_10bit=False,
                    )
                )
                item.media_id = baked_id
                item.source_rel_path = baked_path.relative_to(paths.root).as_posix()
                item.source_abs_path = str(baked_path.resolve())
                item.source_start = 0.0
                item.source_end = baked_dur
                item.speed = 1.0
                item.orientation = canvas_orient
                item.framing = MixedOrientationPolicy.FIT

    if bake_ducked_bgm and export_plan.bgm and Path(export_plan.bgm.path).exists():
        bgm_wav = paths.exports_dir / "bgm_ducked.wav"
        rendered_bgm = render_ducked_bgm_wav(export_plan, bgm_wav)
        if rendered_bgm is not None:
            export_plan.bgm.path = str(rendered_bgm.resolve())
            # Ducking and base level are already baked into bgm_ducked.wav, so keep NLE track at 0dB.
            export_plan.bgm.base_volume_db = 0.0

    export_plan.recompute_timings()
    return export_plan, export_index


def export_episode(
    episode_dir: Path,
    formats: list[str] | None = None,
    stage: str = "fine",
    bake_mixed: bool = True,
    bake_ducked_bgm: bool = True,
) -> dict[str, Path]:
    """Export requested NLE handoff formats from fine_cut.yaml (or rough_cut.yaml)."""
    paths = EpisodePaths(episode_dir)
    media_index = load_media_index(paths.media_index_json)
    plan_path = (
        paths.fine_cut_yaml
        if stage == "fine" and paths.fine_cut_yaml.exists()
        else paths.rough_cut_yaml
    )
    plan = load_edit_plan(plan_path)
    plan.recompute_timings()

    # Ensure V2 overlay PNGs exist before exporting OTIO/FCPXML references
    if plan.overlays:
        materialize_overlays(plan, paths.root, paths.overlays_dir)
        save_edit_plan(plan_path, plan)

    nle_plan, nle_index = _prepare_nle_assets(
        plan,
        media_index,
        paths,
        bake_mixed=bake_mixed,
        bake_ducked_bgm=bake_ducked_bgm,
    )

    wanted = [f.strip().lower() for f in (formats or ["otio", "fcpxml", "srt"]) if f.strip()]
    results: dict[str, Path] = {}

    if "srt" in wanted:
        results["srt"] = write_srt(nle_plan.subtitles, paths.subtitles_srt)
    if "otio" in wanted:
        results["otio"] = export_otio(nle_plan, nle_index, paths.root, paths.timeline_otio)
    if "fcpxml" in wanted:
        results["fcpxml"] = export_fcpxml(nle_plan, nle_index, paths.root, paths.timeline_fcpxml)
    if "jianying" in wanted:
        results["jianying"] = export_jianying_draft(
            nle_plan, nle_index, paths.root, paths.jianying_draft_dir
        )

    return results
