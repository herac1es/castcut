"""Stage 2 (Rough Cut) and Stage 3 (Fine Cut) timeline planner for lure fishing vlogs."""

from __future__ import annotations

from pathlib import Path

from castcut.models import (
    BgmSpec,
    CandidateClip,
    ClipCategory,
    DuckingInterval,
    EditPlan,
    HudOverlaySpec,
    MediaFile,
    MediaTranscript,
    MixedOrientationPolicy,
    ProjectConfig,
    SubtitleCue,
    TemplateType,
    TimelineClipItem,
)
from castcut.workspace import (
    EpisodePaths,
    load_clip_catalog,
    load_edit_plan,
    load_media_index,
    load_project_config,
    load_transcripts,
    save_edit_plan,
)

SECTION_MARKER_COLORS = {
    "hook": "Red",
    "intro_spot": "Cyan",
    "gear_setup": "Purple",
    "search_adjust": "Yellow",
    "climax_catches": "Green",
    "blooper_moment": "Yellow",
    "outro": "Blue",
}


def _trim_edge_silences(
    clip: CandidateClip, media: MediaFile, pad_sec: float = 0.15
) -> tuple[float, float]:
    """Tighten leading/trailing dead air on a clip using detected silence intervals."""
    start, end = clip.start, clip.end
    for sil in media.silences:
        if sil.start <= start + 0.05 and sil.end > start and sil.end < end - 0.8:
            start = max(start, sil.end - pad_sec)
        if sil.end >= end - 0.05 and sil.start < end and sil.start > start + 0.8:
            end = min(end, sil.start + pad_sec)
    if end - start < 0.8:
        return clip.start, clip.end
    return round(start, 3), round(end, 3)


def _select_ordered_clips(
    clips: list[CandidateClip],
    template: TemplateType,
    target_duration: float,
) -> list[tuple[str, CandidateClip, float, tuple[float, float] | None]]:
    """Build lure-vlog narrative order: (section, clip, speed, optional_override_range)."""
    kept = [c for c in clips if c.keep and c.category != ClipCategory.WASTE]
    if not kept:
        kept = list(clips)
    if not kept:
        return []

    by_cat: dict[ClipCategory, list[CandidateClip]] = {}
    for c in kept:
        by_cat.setdefault(c.category, []).append(c)

    selected: list[tuple[str, CandidateClip, float, tuple[float, float] | None]] = []
    used_ids: set[str] = set()
    budget = max(15.0, target_duration)
    accumulated = 0.0

    # 1. Cold-open Hook (3.5s teaser from best catch or blooper — never consumes the full climax clip)
    hook_pool = (
        sorted(by_cat.get(ClipCategory.HOOK_HIGHLIGHT, []), key=lambda c: c.score, reverse=True)
        + sorted(by_cat.get(ClipCategory.FIGHT_CATCH, []), key=lambda c: c.score, reverse=True)
        + sorted(by_cat.get(ClipCategory.BLOOPER, []), key=lambda c: c.score, reverse=True)
    )
    if hook_pool:
        top = hook_pool[0]
        teaser_len = min(4.0, max(1.8, top.duration * 0.45))
        t_start = top.start
        t_end = round(min(top.end, t_start + teaser_len), 3)
        if t_end - t_start >= 1.2:
            selected.append(("hook", top, 1.0, (t_start, t_end)))
            accumulated += t_end - t_start
            if top.category == ClipCategory.HOOK_HIGHLIGHT:
                used_ids.add(top.clip_id)

    def _add_clip(section: str, clip: CandidateClip, speed: float = 1.0) -> bool:
        nonlocal accumulated
        if clip.clip_id in used_ids:
            return False
        est = clip.duration / max(speed, 0.25)
        if accumulated + est > budget * 1.15 and len(selected) >= 3:
            return False
        selected.append((section, clip, speed, None))
        used_ids.add(clip.clip_id)
        accumulated += est
        return True

    if template == TemplateType.SHORT_HIGHLIGHT:
        # Fast-paced vertical/highlight cut: hook -> top catches -> best blooper/talking -> outro
        for c in sorted(by_cat.get(ClipCategory.FIGHT_CATCH, []), key=lambda x: x.score, reverse=True):
            _add_clip("climax_catches", c, 1.0)
        for c in sorted(by_cat.get(ClipCategory.BLOOPER, []), key=lambda x: x.score, reverse=True)[:2]:
            _add_clip("blooper_moment", c, 1.0)
        for c in sorted(by_cat.get(ClipCategory.GEAR_RIG, []), key=lambda x: x.score, reverse=True)[:1]:
            _add_clip("gear_setup", c, 1.0)
        for c in sorted(by_cat.get(ClipCategory.TALKING_STRATEGY, []), key=lambda x: x.score, reverse=True)[:2]:
            _add_clip("intro_spot", c, 1.0)
    else:
        # Full fishing-vlog story arc:
        # Hook -> B-roll/Intro Spot -> Gear/Rig -> Search/Cast (2x) + Blooper -> Catches -> Outro
        brolls = by_cat.get(ClipCategory.BROLL_SCENERY, [])
        talking = by_cat.get(ClipCategory.TALKING_STRATEGY, [])
        gear = by_cat.get(ClipCategory.GEAR_RIG, [])
        casts = by_cat.get(ClipCategory.CASTING_ACTION, [])
        bloopers = by_cat.get(ClipCategory.BLOOPER, [])
        catches = by_cat.get(ClipCategory.FIGHT_CATCH, [])
        unclassified = by_cat.get(ClipCategory.UNCLASSIFIED, [])

        # Establishing scenery + opening talking
        if brolls:
            _add_clip("intro_spot", brolls[0], 1.0)
        if talking:
            _add_clip("intro_spot", talking[0], 1.0)
        for g in gear[:2]:
            _add_clip("gear_setup", g, 1.0)

        # Middle search & adjustment beats (speed up repetitive casts to 1.8x)
        for c in casts[:2]:
            speed = 1.8 if c.duration >= 6.0 and not c.has_speech else 1.0
            _add_clip("search_adjust", c, speed)
        for t in talking[1:-1] if len(talking) > 2 else []:
            _add_clip("search_adjust", t, 1.0)
        for b in bloopers:
            _add_clip("blooper_moment", b, 1.0)

        # Climax catches (preserve chronological order so catch count feels authentic)
        for f in catches:
            _add_clip("climax_catches", f, 1.0)

        # Remaining scenery / unclassified if budget allows
        for extra in brolls[1:] + unclassified:
            _add_clip("search_adjust", extra, 1.0)

        # Closing reflection (last talking clip if unused)
        if len(talking) >= 2 and talking[-1].clip_id not in used_ids:
            _add_clip("outro", talking[-1], 1.0)

    # Fallback: ensure at least something is placed if filters were overly strict
    if not selected:
        for c in kept:
            selected.append(("search_adjust", c, 1.0, None))
    return selected


def has_aspect_mismatch(src_w: int, src_h: int, dst_w: int, dst_h: int, tol: float = 0.08) -> bool:
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return False
    src_ar = src_w / src_h
    dst_ar = dst_w / dst_h
    return abs(src_ar - dst_ar) / dst_ar > tol


def _resolve_framing(
    src_w: int, src_h: int, canvas_w: int, canvas_h: int, policy: MixedOrientationPolicy
) -> MixedOrientationPolicy:
    if not has_aspect_mismatch(src_w, src_h, canvas_w, canvas_h):
        return MixedOrientationPolicy.FIT
    return policy


def _build_subtitles_for_plan(
    plan: EditPlan,
    transcripts: dict[str, MediaTranscript],
) -> list[SubtitleCue]:
    """Map source transcript segments onto exact output timeline timecodes (trim + speed aware)."""
    cues: list[SubtitleCue] = []
    idx = 1
    for item in plan.items:
        tr = transcripts.get(item.media_id)
        if not tr or not tr.segments:
            continue
        for seg in tr.segments:
            ov_s = max(seg.start, item.source_start)
            ov_e = min(seg.end, item.source_end)
            if ov_e - ov_s < 0.15:
                continue
            out_s = item.timeline_start + (ov_s - item.source_start) / item.speed
            out_e = item.timeline_start + (ov_e - item.source_start) / item.speed
            txt = seg.text.strip()
            if not txt:
                continue
            cues.append(
                SubtitleCue(
                    index=idx,
                    start=round(out_s, 3),
                    end=round(max(out_s + 0.25, out_e), 3),
                    text=txt,
                )
            )
            idx += 1
    return cues


def _build_overlays_for_plan(plan: EditPlan, cfg: ProjectConfig) -> list[HudOverlaySpec]:
    """Generate clean transparent HUD overlays for chapters, lure/spot info, and catch counters."""
    overlays: list[HudOverlaySpec] = []
    ov_idx = 1
    catch_count = 0
    seen_sections: set[str] = set()

    for item in plan.items:
        if item.section == "intro_spot" and "intro_spot" not in seen_sections:
            seen_sections.add("intro_spot")
            subtitle = cfg.location or f"目标鱼：{cfg.target_species}"
            overlays.append(
                HudOverlaySpec(
                    overlay_id=f"OV{ov_idx:03d}",
                    kind="chapter",
                    title=cfg.title,
                    subtitle=subtitle,
                    start=round(item.timeline_start + 0.3, 3),
                    duration=min(3.5, max(1.5, item.output_duration - 0.3)),
                    position="bottom_left",
                )
            )
            ov_idx += 1

        if item.section == "gear_setup" and "gear_setup" not in seen_sections:
            seen_sections.add("gear_setup")
            overlays.append(
                HudOverlaySpec(
                    overlay_id=f"OV{ov_idx:03d}",
                    kind="lure_info",
                    title="拟饵与钓组",
                    subtitle=item.note[:26] if item.note else "主攻标点配置",
                    start=round(item.timeline_start + 0.2, 3),
                    duration=min(3.2, max(1.5, item.output_duration - 0.2)),
                    position="top_left",
                )
            )
            ov_idx += 1

        if item.section == "climax_catches" and item.category == ClipCategory.FIGHT_CATCH:
            catch_count += 1
            overlays.append(
                HudOverlaySpec(
                    overlay_id=f"OV{ov_idx:03d}",
                    kind="catch_counter",
                    title=f"中鱼 #{catch_count}",
                    subtitle=cfg.target_species,
                    start=round(item.timeline_start + 0.25, 3),
                    duration=min(3.0, max(1.5, item.output_duration - 0.25)),
                    position="top_right",
                )
            )
            ov_idx += 1

        if item.section == "blooper_moment" and "blooper_moment" not in seen_sections:
            seen_sections.add("blooper_moment")
            overlays.append(
                HudOverlaySpec(
                    overlay_id=f"OV{ov_idx:03d}",
                    kind="callout",
                    title="翻车现场",
                    subtitle=item.note[:22] if item.note else "路亚人的日常",
                    start=round(item.timeline_start + 0.2, 3),
                    duration=min(2.6, max(1.3, item.output_duration - 0.2)),
                    position="top_left",
                )
            )
            ov_idx += 1

    # Persistent low-interference channel watermark aligned with coolbot (@路亚知马力)
    if cfg.channel_handle.strip() and plan.total_duration > 0:
        overlays.append(
            HudOverlaySpec(
                overlay_id=f"OV{ov_idx:03d}",
                kind="watermark",
                title=cfg.channel_handle.strip(),
                subtitle="",
                start=0.0,
                duration=plan.total_duration,
                position="bottom_right",
            )
        )

    return overlays


def _merge_intervals(intervals: list[tuple[float, float]], gap_sec: float = 0.8) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for s, e in ordered[1:]:
        prev_s, prev_e = merged[-1]
        if s <= prev_e + gap_sec:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def _build_bgm_spec(plan: EditPlan, cfg: ProjectConfig, episode_root: Path) -> BgmSpec | None:
    if not cfg.bgm_path:
        return None
    raw_bgm = Path(cfg.bgm_path)
    bgm_abs = raw_bgm if raw_bgm.is_absolute() else (episode_root / raw_bgm).resolve()
    if not bgm_abs.exists():
        return None

    duck_windows: list[tuple[float, float]] = []
    # Duck BGM whenever subtitles/speech or high-energy fight audio plays.
    for cue in plan.subtitles:
        duck_windows.append((max(0.0, cue.start - 0.2), cue.end + 0.25))
    for item in plan.items:
        if item.category in (ClipCategory.TALKING_STRATEGY, ClipCategory.FIGHT_CATCH, ClipCategory.BLOOPER):
            duck_windows.append((item.timeline_start, item.timeline_end))

    merged = _merge_intervals(duck_windows, gap_sec=0.8)
    intervals = [
        DuckingInterval(start=round(s, 3), end=round(e, 3), target_db=cfg.bgm_duck_db)
        for s, e in merged
    ]
    return BgmSpec(
        path=str(bgm_abs),
        base_volume_db=cfg.bgm_volume_db,
        duck_volume_db=cfg.bgm_duck_db,
        ducking_intervals=intervals,
    )


def build_rough_plan(
    episode_dir: Path,
    template_override: TemplateType | None = None,
) -> EditPlan:
    """Stage 2: Assemble rough_cut.yaml from media_index.json and clips.json."""
    paths = EpisodePaths(episode_dir)
    cfg = load_project_config(paths.project_yaml)
    media_index = load_media_index(paths.media_index_json)
    catalog = load_clip_catalog(paths.clips_json)

    media_map = {m.media_id: m for m in media_index.files}
    template = template_override or cfg.template
    width, height = cfg.resolve_dimensions(media_index.dominant_orientation)

    ordered = _select_ordered_clips(catalog.clips, template, cfg.target_duration_sec)
    items: list[TimelineClipItem] = []

    for idx, (section, clip, speed, override_range) in enumerate(ordered, start=1):
        media = media_map[clip.media_id]
        if override_range is not None:
            s_start, s_end = override_range
        else:
            s_start, s_end = _trim_edge_silences(clip, media)

        framing = _resolve_framing(media.width, media.height, width, height, cfg.mixed_orientation)
        marker_label = f"[{section}] {clip.clip_id}"
        if clip.keywords_hit:
            marker_label += f" ({','.join(clip.keywords_hit[:2])})"

        items.append(
            TimelineClipItem(
                item_id=f"T{idx:03d}",
                section=section,
                clip_id=clip.clip_id,
                media_id=clip.media_id,
                source_rel_path=media.rel_path,
                source_abs_path=media.abs_path,
                source_start=s_start,
                source_end=s_end,
                speed=speed,
                source_width=media.width,
                source_height=media.height,
                orientation=clip.orientation,
                framing=framing,
                category=clip.category,
                marker_label=marker_label,
                marker_color=SECTION_MARKER_COLORS.get(section, "Blue"),
                note=clip.agent_note or clip.transcript_text[:48],
            )
        )

    plan = EditPlan(
        episode_id=cfg.episode_id,
        title=cfg.title,
        stage="rough",
        template=template,
        width=width,
        height=height,
        fps=cfg.fps,
        mixed_orientation=cfg.mixed_orientation,
        items=items,
        notes="粗剪单：可直接手工或由 Agent 调整顺序、source_start/source_end、speed 后再执行 fine cut。",
    )
    plan.recompute_timings()
    save_edit_plan(paths.rough_cut_yaml, plan)
    return plan


def build_fine_plan(episode_dir: Path) -> EditPlan:
    """Stage 3: Upgrade rough_cut.yaml into fine_cut.yaml with subtitles, HUD overlays, and BGM ducking."""
    paths = EpisodePaths(episode_dir)
    cfg = load_project_config(paths.project_yaml)
    if not paths.rough_cut_yaml.exists():
        build_rough_plan(episode_dir)

    rough = load_edit_plan(paths.rough_cut_yaml)
    transcripts = load_transcripts(paths.transcripts_dir)

    fine = rough.model_copy(deep=True)
    fine.stage = "fine"
    fine.recompute_timings()
    fine.subtitles = _build_subtitles_for_plan(fine, transcripts)
    fine.overlays = _build_overlays_for_plan(fine, cfg)
    fine.bgm = _build_bgm_spec(fine, cfg, paths.root)
    fine.notes = "精剪单：已同步时间线字幕、透明信息卡（V2 轨）与 BGM 闪避区间。"

    save_edit_plan(paths.fine_cut_yaml, fine)
    return fine
