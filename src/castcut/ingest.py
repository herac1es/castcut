"""Stage 1: Raw footage ingest, probing, analysis, proxies, contact sheets, and initial clip slicing."""

from __future__ import annotations

import json
from pathlib import Path

from castcut.asr import transcribe_media
from castcut.glossary import LureGlossary, load_glossary
from castcut.media import (
    VIDEO_EXTENSIONS,
    detect_audio_peaks,
    detect_scene_cuts,
    detect_silences,
    generate_contact_sheet,
    generate_proxy,
    probe_video,
)
from castcut.models import (
    CandidateClip,
    ClipCatalog,
    ClipCategory,
    MediaFile,
    MediaIndex,
    MediaTranscript,
    ProjectConfig,
)
from castcut.workspace import EpisodePaths, load_project_config


def _discover_raw_videos(raw_dir: Path) -> list[Path]:
    if not raw_dir.exists():
        return []
    files = [
        p
        for p in raw_dir.rglob("*")
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    files.sort(key=lambda p: str(p.relative_to(raw_dir)).lower())
    return files


def _invert_silences(
    duration: float, silences: list[tuple[float, float]], min_active: float = 1.2
) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    if not silences:
        return [(0.0, duration)]
    active: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted(silences):
        if s_start - cursor >= min_active:
            active.append((round(cursor, 3), round(s_start, 3)))
        cursor = max(cursor, s_end)
    if duration - cursor >= min_active:
        active.append((round(cursor, 3), round(duration, 3)))
    return active


def _build_candidate_windows(
    media: MediaFile,
    transcript: MediaTranscript,
    max_window_sec: float = 28.0,
) -> list[tuple[float, float]]:
    """Produce meaningful sub-clip boundaries from transcript utterances, audio peaks, and scene cuts."""
    if media.duration <= 0.4:
        return []

    # Short standalone clips (<= 20s) usually represent a single intentional shot.
    if media.duration <= 20.0 and len(media.scene_cuts) <= 1:
        return [(0.0, media.duration)]

    windows: list[tuple[float, float]] = []

    if transcript.segments:
        # Group adjacent transcript segments separated by <= 1.8s into coherent speech beats.
        cur_s = max(0.0, transcript.segments[0].start - 0.2)
        cur_e = min(media.duration, transcript.segments[0].end + 0.25)
        for seg in transcript.segments[1:]:
            if seg.start - cur_e <= 1.8 and (seg.end - cur_s) <= max_window_sec:
                cur_e = min(media.duration, seg.end + 0.25)
            else:
                windows.append((round(cur_s, 3), round(cur_e, 3)))
                cur_s = max(0.0, seg.start - 0.2)
                cur_e = min(media.duration, seg.end + 0.25)
        windows.append((round(cur_s, 3), round(cur_e, 3)))

    # Add non-overlapping audio peak windows (critical for silent-speech hooksets / splashes / drag).
    for peak in media.audio_peaks:
        p_start = round(max(0.0, peak.time - 2.5), 3)
        p_end = round(min(media.duration, peak.time + 5.5), 3)
        overlaps = any(not (p_end <= w_s or p_start >= w_e) for w_s, w_e in windows)
        if not overlaps and (p_end - p_start) >= 1.5:
            windows.append((p_start, p_end))

    # If still no windows (e.g. silent B-roll / no ASR), split by scene cuts or active audio blocks.
    if not windows:
        if media.scene_cuts:
            pts = [0.0, *media.scene_cuts, media.duration]
            for a, b in zip(pts, pts[1:], strict=False):
                if b - a >= 1.5:
                    windows.append((round(a, 3), round(b, 3)))
        else:
            sil_pairs = [(s.start, s.end) for s in media.silences]
            windows = _invert_silences(media.duration, sil_pairs, min_active=1.5)
            if not windows:
                windows = [(0.0, media.duration)]

    # Split overly long windows so triage stays granular.
    final_windows: list[tuple[float, float]] = []
    for w_s, w_e in sorted(windows):
        if w_e - w_s <= max_window_sec * 1.4:
            final_windows.append((w_s, w_e))
            continue
        cur = w_s
        while cur < w_e:
            nxt = min(w_e, cur + max_window_sec)
            if nxt - cur >= 1.5:
                final_windows.append((round(cur, 3), round(nxt, 3)))
            cur = nxt
    return final_windows


def _score_candidate(
    media: MediaFile,
    start: float,
    end: float,
    transcript: MediaTranscript,
    glossary: LureGlossary,
) -> CandidateClip:
    seg_texts = [
        s.text for s in transcript.segments if s.end > start and s.start < end and s.text.strip()
    ]
    joined = " ".join(seg_texts).strip()
    has_speech = bool(joined)
    keywords_hit, kw_cat = glossary.match_keywords(joined)

    window_peaks = [p.db for p in media.audio_peaks if start <= p.time <= end]
    peak_db = max(window_peaks) if window_peaks else None

    # Heuristic baseline category and score (designed to be refined by Antigravity or Codex).
    score = 0.40
    category = ClipCategory.UNCLASSIFIED

    if kw_cat is not None:
        category = kw_cat
        if kw_cat == ClipCategory.FIGHT_CATCH:
            score += 0.42
        elif kw_cat == ClipCategory.BLOOPER:
            score += 0.35
        elif kw_cat in (ClipCategory.TALKING_STRATEGY, ClipCategory.GEAR_RIG):
            score += 0.25
    elif has_speech:
        category = ClipCategory.TALKING_STRATEGY
        score += 0.20
    elif peak_db is not None and peak_db >= -18.0:
        category = ClipCategory.FIGHT_CATCH
        score += 0.30
    elif (end - start) <= 8.0:
        category = ClipCategory.BROLL_SCENERY
        score += 0.08
    else:
        category = ClipCategory.CASTING_ACTION
        score += 0.05

    if peak_db is not None:
        score += min(0.15, max(0.0, (peak_db + 26.0) / 100.0))

    # Hint from filename conventions (e.g. catch_01.mp4, broll_lake.mp4, intro.mp4).
    fname_lower = media.filename.lower()
    if any(k in fname_lower for k in ("catch", "fish", "fight", "hook", "中鱼", "高光")):
        if category in (ClipCategory.UNCLASSIFIED, ClipCategory.CASTING_ACTION, ClipCategory.BROLL_SCENERY):
            category = ClipCategory.FIGHT_CATCH
        score = max(score, 0.85)
    elif any(k in fname_lower for k in ("broll", "scenery", "drone", "空镜", "风景")):
        if not has_speech:
            category = ClipCategory.BROLL_SCENERY
            score = max(score, 0.55)
    elif any(k in fname_lower for k in ("lure", "rig", "gear", "拟饵", "钓组", "装备")):
        if category == ClipCategory.UNCLASSIFIED:
            category = ClipCategory.GEAR_RIG
        score = max(score, 0.68)
    elif any(k in fname_lower for k in ("blooper", "fail", "snag", "挂底", "炸线", "跑鱼")):
        category = ClipCategory.BLOOPER
        score = max(score, 0.78)

    score = round(min(1.0, max(0.05, score)), 2)
    return CandidateClip(
        clip_id="",
        media_id=media.media_id,
        source_rel_path=media.rel_path,
        start=round(start, 3),
        end=round(end, 3),
        duration=round(max(0.0, end - start), 3),
        width=media.width,
        height=media.height,
        orientation=media.orientation,
        category=category,
        score=score,
        transcript_text=joined,
        keywords_hit=keywords_hit,
        peak_db=peak_db,
        has_speech=has_speech,
        keep=category != ClipCategory.WASTE,
    )


def ingest_episode(
    episode_dir: Path,
    asr_mode: str = "auto",
    whisper_model: str = "small",
    make_proxies: bool = True,
    make_contacts: bool = True,
) -> tuple[MediaIndex, ClipCatalog]:
    """Run Stage 1 ingest on all raw videos in an episode directory."""
    paths = EpisodePaths(episode_dir)
    paths.ensure_dirs()
    cfg: ProjectConfig = load_project_config(paths.project_yaml)
    glossary = load_glossary(cfg.glossary_path)

    raw_files = _discover_raw_videos(paths.raw_dir)
    if not raw_files:
        raise FileNotFoundError(
            f"No video files found in {paths.raw_dir}. Drop raw footage (.mp4/.mov/...) into raw/ first."
        )

    media_files: list[MediaFile] = []
    all_clips: list[CandidateClip] = []
    clip_counter = 1

    for idx, video_path in enumerate(raw_files, start=1):
        media_id = f"M{idx:03d}"
        rel_path = video_path.relative_to(paths.root).as_posix()
        media = probe_video(video_path, media_id=media_id, rel_path=rel_path)

        if media.has_audio:
            media.silences = detect_silences(video_path, total_duration=media.duration)
            media.audio_peaks = detect_audio_peaks(video_path)
        media.scene_cuts = detect_scene_cuts(video_path)

        if make_proxies:
            proxy_path = paths.proxies_dir / f"{media_id}_{video_path.stem}_proxy.mp4"
            generate_proxy(video_path, proxy_path)
            media.proxy_rel_path = proxy_path.relative_to(paths.root).as_posix()

        if make_contacts:
            sheet_path = paths.contacts_dir / f"{media_id}_{video_path.stem}_sheet.jpg"
            sheet_title = (
                f"{media_id} | {media.filename} | {media.width}x{media.height} "
                f"({media.orientation}) | {media.duration:.1f}s"
            )
            generate_contact_sheet(
                video_path, sheet_path, duration=media.duration, title=sheet_title
            )
            media.contact_sheet_rel_path = sheet_path.relative_to(paths.root).as_posix()

        transcript = transcribe_media(
            video_path,
            media_id=media_id,
            glossary=glossary,
            mode=asr_mode,
            model_size=whisper_model,
        )
        t_path = paths.transcripts_dir / f"{media_id}.json"
        t_path.write_text(
            json.dumps(transcript.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        windows = _build_candidate_windows(media, transcript)
        for w_s, w_e in windows:
            cand = _score_candidate(media, w_s, w_e, transcript, glossary)
            cand.clip_id = f"C{clip_counter:03d}"
            clip_counter += 1
            all_clips.append(cand)

        media_files.append(media)

    land_dur = round(sum(m.duration for m in media_files if m.orientation != "portrait"), 3)
    port_dur = round(sum(m.duration for m in media_files if m.orientation == "portrait"), 3)
    dominant = "portrait" if port_dur > land_dur else "landscape"

    media_index = MediaIndex(
        episode_id=cfg.episode_id,
        dominant_orientation=dominant,
        landscape_duration=land_dur,
        portrait_duration=port_dur,
        total_duration=round(sum(m.duration for m in media_files), 3),
        has_10bit_media=any(m.is_10bit for m in media_files),
        files=media_files,
    )
    catalog = ClipCatalog(episode_id=cfg.episode_id, clips=all_clips)

    paths.media_index_json.write_text(
        json.dumps(media_index.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths.clips_json.write_text(
        json.dumps(catalog.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return media_index, catalog
