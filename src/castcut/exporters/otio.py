"""Pure-Python OpenTimelineIO (OTIO) Timeline.1 JSON exporter for DaVinci Resolve Free."""

from __future__ import annotations

import json
from pathlib import Path

from castcut.models import EditPlan, MediaIndex

OTIO_MARKER_COLORS = {
    "Blue": "BLUE",
    "Cyan": "CYAN",
    "Green": "GREEN",
    "Yellow": "YELLOW",
    "Red": "RED",
    "Purple": "PURPLE",
}


def _rt(seconds: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": float(fps),
        "value": float(round(seconds * fps)),
    }


def _tr(start_sec: float, dur_sec: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rt(start_sec, fps),
        "duration": _rt(max(1.0 / fps, dur_sec), fps),
    }


def _ext_ref(abs_path: Path, avail_dur: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "ExternalReference.1",
        "metadata": {},
        "name": abs_path.name,
        "available_range": _tr(0.0, max(avail_dur, 1.0 / fps), fps),
        "target_url": abs_path.resolve().as_uri(),
    }


def _gap(dur_sec: float, fps: float) -> dict:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "metadata": {},
        "name": "",
        "source_range": _tr(0.0, dur_sec, fps),
        "effects": [],
        "markers": [],
        "enabled": True,
    }


def export_otio(
    plan: EditPlan,
    media_index: MediaIndex,
    episode_root: Path,
    dest: Path,
) -> Path:
    """Export an OpenTimelineIO 1.x JSON file that DaVinci Resolve Free imports natively."""
    fps = plan.fps
    media_map = {m.media_id: m for m in media_index.files}

    v1_children: list[dict] = []
    a1_children: list[dict] = []

    for item in plan.items:
        media = media_map.get(item.media_id)
        src_path = Path(item.source_abs_path)
        if not src_path.exists():
            src_path = (episode_root / item.source_rel_path).resolve()
        avail_dur = media.duration if media else max(item.source_end, 1.0)

        effects: list[dict] = []
        if abs(item.speed - 1.0) >= 1e-3:
            effects.append(
                {
                    "OTIO_SCHEMA": "LinearTimeWarp.1",
                    "metadata": {},
                    "name": f"Speed {item.speed:.2f}x",
                    "effect_name": "LinearTimeWarp",
                    "time_scalar": float(item.speed),
                }
            )

        markers: list[dict] = []
        if item.marker_label:
            markers.append(
                {
                    "OTIO_SCHEMA": "Marker.2",
                    "metadata": {"section": item.section, "category": item.category.value},
                    "name": item.marker_label,
                    "marked_range": _tr(item.source_start, 1.0 / fps, fps),
                    "color": OTIO_MARKER_COLORS.get(item.marker_color, "BLUE"),
                    "comment": item.note,
                }
            )

        clip_common = {
            "OTIO_SCHEMA": "Clip.2",
            "metadata": {
                "castcut": {
                    "item_id": item.item_id,
                    "clip_id": item.clip_id,
                    "section": item.section,
                    "category": item.category.value,
                    "framing": item.framing.value,
                }
            },
            "name": f"{item.item_id}_{src_path.stem}",
            "source_range": _tr(item.source_start, item.source_duration, fps),
            "media_reference": _ext_ref(src_path, avail_dur, fps),
            "effects": effects,
            "markers": markers,
            "enabled": True,
        }
        v1_children.append(clip_common)

        if media and media.has_audio and not item.mute_audio:
            a1_children.append(
                {
                    **clip_common,
                    "markers": [],
                }
            )
        else:
            a1_children.append(_gap(item.output_duration, fps))

    # Track V2: Timed HUD cards (non-overlapping with Gaps); Track V3: Persistent channel watermark
    v2_children: list[dict] = []
    v3_children: list[dict] = []
    v2_cursor = 0.0
    for ov in sorted(plan.overlays, key=lambda o: o.start):
        if not ov.png_rel_path:
            continue
        png_path = (episode_root / ov.png_rel_path).resolve()
        if not png_path.exists():
            continue
        ov_clip = {
            "OTIO_SCHEMA": "Clip.2",
            "metadata": {"castcut_overlay": ov.model_dump(mode="json")},
            "name": f"{ov.overlay_id}_{ov.title}",
            "source_range": _tr(0.0, ov.duration, fps),
            "media_reference": _ext_ref(png_path, ov.duration, fps),
            "effects": [],
            "markers": [],
            "enabled": True,
        }
        if ov.kind == "watermark":
            v3_children.append(ov_clip)
            continue
        if ov.start > v2_cursor + (0.5 / fps):
            v2_children.append(_gap(ov.start - v2_cursor, fps))
            v2_cursor = ov.start
        v2_children.append(ov_clip)
        v2_cursor += ov.duration

    tracks: list[dict] = [
        {
            "OTIO_SCHEMA": "Track.1",
            "metadata": {},
            "name": "V1 Main",
            "kind": "Video",
            "children": v1_children,
            "effects": [],
            "markers": [],
            "enabled": True,
        }
    ]
    if v2_children:
        tracks.append(
            {
                "OTIO_SCHEMA": "Track.1",
                "metadata": {},
                "name": "V2 HUD Overlays",
                "kind": "Video",
                "children": v2_children,
                "effects": [],
                "markers": [],
                "enabled": True,
            }
        )
    if v3_children:
        tracks.append(
            {
                "OTIO_SCHEMA": "Track.1",
                "metadata": {},
                "name": "V3 Channel Handle",
                "kind": "Video",
                "children": v3_children,
                "effects": [],
                "markers": [],
                "enabled": True,
            }
        )

    tracks.append(
        {
            "OTIO_SCHEMA": "Track.1",
            "metadata": {},
            "name": "A1 Dialogue & Field Audio",
            "kind": "Audio",
            "children": a1_children,
            "effects": [],
            "markers": [],
            "enabled": True,
        }
    )

    if plan.bgm and Path(plan.bgm.path).exists():
        bgm_path = Path(plan.bgm.path).resolve()
        tracks.append(
            {
                "OTIO_SCHEMA": "Track.1",
                "metadata": {"ducking_intervals": [d.model_dump() for d in plan.bgm.ducking_intervals]},
                "name": "A2 BGM",
                "kind": "Audio",
                "children": [
                    {
                        "OTIO_SCHEMA": "Clip.2",
                        "metadata": {
                            "base_volume_db": plan.bgm.base_volume_db,
                            "duck_volume_db": plan.bgm.duck_volume_db,
                        },
                        "name": bgm_path.stem,
                        "source_range": _tr(0.0, plan.total_duration, fps),
                        "media_reference": _ext_ref(bgm_path, plan.total_duration, fps),
                        "effects": [],
                        "markers": [],
                        "enabled": True,
                    }
                ],
                "effects": [],
                "markers": [],
                "enabled": True,
            }
        )

    timeline = {
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {
            "castcut_version": "0.1.0",
            "episode_id": plan.episode_id,
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "mixed_orientation": plan.mixed_orientation.value,
        },
        "name": plan.title,
        "global_start_time": _rt(0.0, fps),
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "metadata": {},
            "name": "tracks",
            "children": tracks,
            "effects": [],
            "markers": [],
            "enabled": True,
        },
    }

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
