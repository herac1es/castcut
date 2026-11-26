"""Episode-level Doubao TTS voiceover orchestration: synthesize MP3s, sync subtitles, update BGM ducking."""

from __future__ import annotations

from pathlib import Path

from castcut.exporters.srt import write_srt
from castcut.models import DuckingInterval, EditPlan, SubtitleCue, VoiceoverCue
from castcut.planner import _merge_intervals, build_fine_plan
from castcut.tts import DoubaoTtsClient, load_doubao_tts_config
from castcut.workspace import EpisodePaths, load_edit_plan, load_project_config, save_edit_plan


def _promote_clip_voiceovers(plan: EditPlan) -> None:
    """Convert any TimelineClipItem.voiceover_text into explicit VoiceoverCue entries on plan.voiceovers."""
    by_item_id = {vo.item_id: vo for vo in plan.voiceovers if vo.item_id}
    next_idx = len(plan.voiceovers) + 1
    for item in plan.items:
        txt = (item.voiceover_text or "").strip()
        if not txt:
            continue
        existing = by_item_id.get(item.item_id)
        if existing is not None:
            existing.text = txt
            existing.start = round(item.timeline_start + 0.12, 3)
        else:
            plan.voiceovers.append(
                VoiceoverCue(
                    vo_id=f"VO{next_idx:03d}",
                    text=txt,
                    item_id=item.item_id,
                    start=round(item.timeline_start + 0.12, 3),
                )
            )
            next_idx += 1

    # Re-anchor any voiceover bound to an item_id whose start is unset
    item_map = {it.item_id: it for it in plan.items}
    for vo in plan.voiceovers:
        if vo.item_id and vo.item_id in item_map and vo.start <= 0.0:
            vo.start = round(item_map[vo.item_id].timeline_start + 0.12, 3)


def _merge_tts_subtitles(plan: EditPlan) -> list[SubtitleCue]:
    """Combine retained field-audio ASR cues with newly synthesized Doubao TTS word-level cues."""
    combined: list[SubtitleCue] = [c for c in plan.subtitles if c.source != "tts"]
    for vo in plan.voiceovers:
        for cue in vo.subtitles:
            combined.append(
                SubtitleCue(
                    index=0,
                    start=round(vo.start + cue.start, 3),
                    end=round(vo.start + cue.end, 3),
                    text=cue.text,
                    source="tts",
                )
            )
    combined.sort(key=lambda c: (c.start, c.end))
    for idx, cue in enumerate(combined, start=1):
        cue.index = idx
    return combined


def _refresh_bgm_ducking_with_voiceovers(plan: EditPlan) -> None:
    if plan.bgm is None or not plan.voiceovers:
        return
    windows = [(iv.start, iv.end) for iv in plan.bgm.ducking_intervals]
    for vo in plan.voiceovers:
        if vo.duration > 0:
            windows.append((max(0.0, vo.start - 0.15), vo.start + vo.duration + 0.25))
    merged = _merge_intervals(windows, gap_sec=0.8)
    plan.bgm.ducking_intervals = [
        DuckingInterval(start=round(s, 3), end=round(e, 3), target_db=plan.bgm.duck_volume_db)
        for s, e in merged
    ]


def synthesize_episode_voiceovers(
    episode_dir: Path,
    *,
    stage: str = "fine",
    client: DoubaoTtsClient | None = None,
    speaker_override: str | None = None,
    context_override: str | None = None,
) -> tuple[EditPlan, int, int]:
    """Synthesize all voiceovers in fine_cut.yaml (or rough_cut.yaml) using Doubao TTS.

    Returns (updated_plan, synthesized_count, cache_hit_count).
    """
    paths = EpisodePaths(episode_dir)
    paths.ensure_dirs()
    cfg = load_project_config(paths.project_yaml)

    plan_path = paths.fine_cut_yaml if stage == "fine" else paths.rough_cut_yaml
    if not plan_path.exists() and stage == "fine":
        build_fine_plan(episode_dir)
    plan = load_edit_plan(plan_path)
    plan.recompute_timings()
    _promote_clip_voiceovers(plan)

    if not plan.voiceovers:
        return plan, 0, 0

    if client is None:
        tts_cfg = load_doubao_tts_config()
        client = DoubaoTtsClient(tts_cfg, cache_dir=paths.tts_cache_dir)

    synth_count = 0
    cache_hits = 0
    for vo in plan.voiceovers:
        if not vo.text.strip():
            continue
        dest_mp3 = paths.voiceovers_dir / f"{vo.vo_id}.mp3"
        speaker = speaker_override or vo.speaker or cfg.tts_voice or client.config.voice
        ctx = context_override if context_override is not None else (
            vo.context_text if vo.context_text is not None else cfg.tts_context_text
        )
        res = client.synthesize(
            vo.text,
            dest_mp3,
            speaker=speaker,
            context_text=ctx,
        )
        vo.speaker = speaker
        vo.duration = res.duration_sec
        vo.audio_rel_path = dest_mp3.relative_to(paths.root).as_posix()
        vo.subtitles = res.subtitles
        synth_count += 1
        if res.from_cache:
            cache_hits += 1

    plan.subtitles = _merge_tts_subtitles(plan)
    _refresh_bgm_ducking_with_voiceovers(plan)
    save_edit_plan(plan_path, plan)
    if plan.subtitles:
        write_srt(plan.subtitles, paths.subtitles_srt)

    return plan, synth_count, cache_hits
