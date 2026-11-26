"""Zero-VIP NLE export orchestrator (OTIO, FCPXML, SRT, optional Jianying baseline draft)."""

from __future__ import annotations

from pathlib import Path

from castcut.exporters.fcpxml import export_fcpxml
from castcut.exporters.jianying import export_jianying_draft
from castcut.exporters.otio import export_otio
from castcut.exporters.srt import write_srt
from castcut.overlays import materialize_overlays
from castcut.workspace import EpisodePaths, load_edit_plan, load_media_index, save_edit_plan


def export_episode(
    episode_dir: Path,
    formats: list[str] | None = None,
    stage: str = "fine",
) -> dict[str, Path]:
    """Export requested NLE handoff formats from fine_cut.yaml (or rough_cut.yaml)."""
    paths = EpisodePaths(episode_dir)
    media_index = load_media_index(paths.media_index_json)
    plan_path = paths.fine_cut_yaml if stage == "fine" and paths.fine_cut_yaml.exists() else paths.rough_cut_yaml
    plan = load_edit_plan(plan_path)
    plan.recompute_timings()

    # Ensure V2 overlay PNGs exist before exporting OTIO/FCPXML references
    if plan.overlays:
        materialize_overlays(plan, paths.root, paths.overlays_dir)
        save_edit_plan(plan_path, plan)

    wanted = [f.strip().lower() for f in (formats or ["otio", "fcpxml", "srt"]) if f.strip()]
    results: dict[str, Path] = {}

    if "srt" in wanted:
        results["srt"] = write_srt(plan.subtitles, paths.subtitles_srt)
    if "otio" in wanted:
        results["otio"] = export_otio(plan, media_index, paths.root, paths.timeline_otio)
    if "fcpxml" in wanted:
        results["fcpxml"] = export_fcpxml(plan, media_index, paths.root, paths.timeline_fcpxml)
    if "jianying" in wanted:
        results["jianying"] = export_jianying_draft(plan, media_index, paths.root, paths.jianying_draft_dir)

    return results
