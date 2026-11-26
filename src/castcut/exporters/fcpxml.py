"""FCPXML 1.10 exporter compatible with DaVinci Resolve Free, Final Cut Pro, and Premiere Pro."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from castcut.models import EditPlan, MediaIndex


def _timebase(fps: float) -> tuple[int, int]:
    """Return exact rational (num, den) frameDuration for standard film/broadcast/action-cam rates."""
    if abs(fps - 23.976) < 0.02:
        return (1001, 24000)
    if abs(fps - 29.97) < 0.02:
        return (1001, 30000)
    if abs(fps - 59.94) < 0.02:
        return (1001, 60000)
    return (1, max(1, int(round(fps))))


def _fmt_time(seconds: float, frame_num: int, frame_den: int) -> str:
    fps_exact = frame_den / frame_num
    frames = max(0, int(round(seconds * fps_exact)))
    if frames == 0:
        return "0s"
    return f"{frames * frame_num}/{frame_den}s"


def _fmt_dur(seconds: float, frame_num: int, frame_den: int) -> str:
    fps_exact = frame_den / frame_num
    frames = max(1, int(round(seconds * fps_exact)))
    return f"{frames * frame_num}/{frame_den}s"


def export_fcpxml(
    plan: EditPlan,
    media_index: MediaIndex,
    episode_root: Path,
    dest: Path,
) -> Path:
    """Export a clean FCPXML 1.10 timeline with clips, speed conform, markers, V2 HUD overlays, and BGM."""
    frame_num, frame_den = _timebase(plan.fps)
    frame_dur = f"{frame_num}/{frame_den}s"
    media_map = {m.media_id: m for m in media_index.files}

    fcpxml = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(fcpxml, "resources")

    fmt_name = f"FFVideoFormat{plan.height}p{int(round(plan.fps))}"
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name=fmt_name,
        frameDuration=frame_dur,
        width=str(plan.width),
        height=str(plan.height),
        colorSpace="1-1-1 (Rec. 709)",
    )

    # Register unique video, overlay, and BGM assets
    next_id = 2
    media_asset_ids: dict[str, str] = {}
    for item in plan.items:
        if item.media_id in media_asset_ids:
            continue
        rid = f"r{next_id}"
        next_id += 1
        media_asset_ids[item.media_id] = rid
        media = media_map.get(item.media_id)
        src_path = Path(item.source_abs_path)
        if not src_path.exists():
            src_path = (episode_root / item.source_rel_path).resolve()
        avail_dur = media.duration if media else max(item.source_end, 1.0)
        has_audio = "1" if (media is None or media.has_audio) else "0"
        asset = ET.SubElement(
            resources,
            "asset",
            id=rid,
            name=src_path.stem,
            start="0s",
            duration=_fmt_dur(avail_dur, frame_num, frame_den),
            hasVideo="1",
            hasAudio=has_audio,
            format="r1",
        )
        ET.SubElement(asset, "media-rep", kind="original-media", src=src_path.resolve().as_uri())

    overlay_asset_ids: dict[str, str] = {}
    for ov in plan.overlays:
        if not ov.png_rel_path:
            continue
        png_path = (episode_root / ov.png_rel_path).resolve()
        if not png_path.exists():
            continue
        rid = f"r{next_id}"
        next_id += 1
        overlay_asset_ids[ov.overlay_id] = rid
        asset = ET.SubElement(
            resources,
            "asset",
            id=rid,
            name=f"{ov.overlay_id}_{ov.title}",
            start="0s",
            duration="0s",
            hasVideo="1",
            hasAudio="0",
            format="r1",
        )
        ET.SubElement(asset, "media-rep", kind="original-media", src=png_path.as_uri())

    bgm_rid: str | None = None
    if plan.bgm and Path(plan.bgm.path).exists():
        bgm_path = Path(plan.bgm.path).resolve()
        bgm_rid = f"r{next_id}"
        next_id += 1
        asset = ET.SubElement(
            resources,
            "asset",
            id=bgm_rid,
            name=bgm_path.stem,
            start="0s",
            duration=_fmt_dur(plan.total_duration, frame_num, frame_den),
            hasVideo="0",
            hasAudio="1",
            audioSources="1",
            audioChannels="2",
            audioRate="48000",
        )
        ET.SubElement(asset, "media-rep", kind="original-media", src=bgm_path.as_uri())

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name=plan.episode_id)
    project = ET.SubElement(event, "project", name=plan.title)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_fmt_dur(plan.total_duration, frame_num, frame_den),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    spine_elements: list[tuple[ET.Element, float, float, float, float]] = []
    for item in plan.items:
        rid = media_asset_ids[item.media_id]
        src_path = Path(item.source_abs_path)
        attrs = {
            "ref": rid,
            "offset": _fmt_time(item.timeline_start, frame_num, frame_den),
            "name": f"{item.item_id}_{src_path.stem}",
            "start": _fmt_time(item.source_start, frame_num, frame_den),
            "duration": _fmt_dur(item.output_duration, frame_num, frame_den),
            "tcFormat": "NDF",
        }
        clip_el = ET.SubElement(spine, "asset-clip", **attrs)

        if abs(item.speed - 1.0) >= 1e-3:
            tm = ET.SubElement(clip_el, "timeMap")
            ET.SubElement(tm, "timept", time="0s", value=_fmt_time(item.source_start, frame_num, frame_den), interp="linear")
            ET.SubElement(
                tm,
                "timept",
                time=_fmt_dur(item.output_duration, frame_num, frame_den),
                value=_fmt_time(item.source_end, frame_num, frame_den),
                interp="linear",
            )

        if item.mute_audio:
            ET.SubElement(clip_el, "adjust-volume", amount="-96dB")
        elif abs(item.audio_gain_db) >= 0.1:
            ET.SubElement(clip_el, "adjust-volume", amount=f"{item.audio_gain_db:.1f}dB")

        if item.marker_label:
            ET.SubElement(
                clip_el,
                "marker",
                start=_fmt_time(item.source_start, frame_num, frame_den),
                duration=frame_dur,
                value=item.marker_label,
                note=item.note,
            )

        spine_elements.append(
            (clip_el, item.timeline_start, item.timeline_end, item.source_start, item.speed)
        )

    # Attach V2 HUD cards (lane 1) and V3 persistent channel watermark (lane 2)
    for ov in plan.overlays:
        rid = overlay_asset_ids.get(ov.overlay_id)
        if not rid or not spine_elements:
            continue
        host = spine_elements[0]
        if ov.kind != "watermark":
            for cand in spine_elements:
                if cand[1] <= ov.start < cand[2]:
                    host = cand
                    break
        host_el, host_tl_start, _, host_src_start, host_speed = host
        rel_offset = host_src_start + max(0.0, ov.start - host_tl_start) * host_speed
        ET.SubElement(
            host_el,
            "video",
            ref=rid,
            lane="2" if ov.kind == "watermark" else "1",
            offset=_fmt_time(rel_offset, frame_num, frame_den),
            name=f"{ov.overlay_id}_{ov.title}",
            start="0s",
            duration=_fmt_dur(ov.duration, frame_num, frame_den),
        )

    # Attach BGM track to the first spine clip on lane -1
    if bgm_rid and plan.bgm and spine_elements:
        first_el, _, _, first_src_start, _ = spine_elements[0]
        bgm_el = ET.SubElement(
            first_el,
            "asset-clip",
            ref=bgm_rid,
            lane="-1",
            offset=_fmt_time(first_src_start, frame_num, frame_den),
            name=Path(plan.bgm.path).stem,
            start="0s",
            duration=_fmt_dur(plan.total_duration, frame_num, frame_den),
            role="music",
        )
        ET.SubElement(bgm_el, "adjust-volume", amount=f"{plan.bgm.base_volume_db:.1f}dB")

    ET.indent(fcpxml, space="  ")
    xml_body = ET.tostring(fcpxml, encoding="unicode")
    full_xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{xml_body}\n'

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(full_xml, encoding="utf-8")
    return dest
