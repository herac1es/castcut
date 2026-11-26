"""Optional zero-VIP Jianying baseline draft exporter (free video/audio/plain-text tracks only)."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

from castcut.models import EditPlan, MediaIndex

US = 1_000_000


def _us(seconds: float) -> int:
    return max(0, int(round(seconds * US)))


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def export_jianying_draft(
    plan: EditPlan,
    media_index: MediaIndex,
    episode_root: Path,
    draft_dir: Path,
) -> Path:
    """Export a strictly zero-VIP Jianying draft using only baseline free video, audio, and plain text."""
    draft_dir.mkdir(parents=True, exist_ok=True)
    media_map = {m.media_id: m for m in media_index.files}

    video_materials: list[dict] = []
    audio_materials: list[dict] = []
    text_materials: list[dict] = []
    speed_materials: list[dict] = []

    video_segments: list[dict] = []
    bgm_segments: list[dict] = []
    text_segments: list[dict] = []

    material_cache: dict[str, str] = {}

    for item in plan.items:
        src_path = Path(item.source_abs_path)
        if not src_path.exists():
            src_path = (episode_root / item.source_rel_path).resolve()
        media = media_map.get(item.media_id)

        if item.media_id not in material_cache:
            mat_id = _uid()
            material_cache[item.media_id] = mat_id
            video_materials.append(
                {
                    "id": mat_id,
                    "type": "video",
                    "material_name": src_path.name,
                    "path": str(src_path),
                    "width": media.width if media else plan.width,
                    "height": media.height if media else plan.height,
                    "duration": _us(media.duration if media else item.source_end),
                    "has_audio": media.has_audio if media else True,
                }
            )
        mat_id = material_cache[item.media_id]

        extra_refs: list[str] = []
        if abs(item.speed - 1.0) >= 1e-3:
            sp_id = _uid()
            speed_materials.append(
                {"id": sp_id, "type": "speed", "mode": 0, "speed": round(item.speed, 3)}
            )
            extra_refs.append(sp_id)

        video_segments.append(
            {
                "id": _uid(),
                "material_id": mat_id,
                "extra_material_refs": extra_refs,
                "speed": round(item.speed, 3),
                "volume": 0.0 if item.mute_audio else round(math.pow(10.0, item.audio_gain_db / 20.0), 3),
                "source_timerange": {
                    "start": _us(item.source_start),
                    "duration": _us(item.source_duration),
                },
                "target_timerange": {
                    "start": _us(item.timeline_start),
                    "duration": _us(item.output_duration),
                },
                "clip": {
                    "alpha": 1.0,
                    "flip": {"horizontal": False, "vertical": False},
                    "rotation": 0.0,
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": 0.0, "y": 0.0},
                },
                "visible": True,
            }
        )

    # Free plain-text subtitles (zero VIP font/flower/animation templates)
    for cue in plan.subtitles:
        txt_id = _uid()
        content_json = json.dumps(
            {
                "text": cue.text,
                "styles": [
                    {
                        "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}}},
                        "size": 7.0,
                        "range": [0, len(cue.text)],
                    }
                ],
            },
            ensure_ascii=False,
        )
        text_materials.append(
            {
                "id": txt_id,
                "type": "subtitle",
                "content": content_json,
                "alignment": 1,
                "font_size": 7.0,
            }
        )
        text_segments.append(
            {
                "id": _uid(),
                "material_id": txt_id,
                "target_timerange": {
                    "start": _us(cue.start),
                    "duration": _us(max(0.2, cue.end - cue.start)),
                },
                "clip": {
                    "alpha": 1.0,
                    "rotation": 0.0,
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": 0.0, "y": -0.78},
                },
                "visible": True,
            }
        )

    if plan.bgm and Path(plan.bgm.path).exists():
        bgm_path = Path(plan.bgm.path).resolve()
        bgm_id = _uid()
        audio_materials.append(
            {
                "id": bgm_id,
                "type": "extract_music",
                "name": bgm_path.stem,
                "path": str(bgm_path),
                "duration": _us(plan.total_duration),
            }
        )
        bgm_segments.append(
            {
                "id": _uid(),
                "material_id": bgm_id,
                "volume": round(math.pow(10.0, plan.bgm.base_volume_db / 20.0), 3),
                "source_timerange": {"start": 0, "duration": _us(plan.total_duration)},
                "target_timerange": {"start": 0, "duration": _us(plan.total_duration)},
                "visible": True,
            }
        )

    tracks = [
        {"id": _uid(), "type": "video", "attribute": 0, "segments": video_segments},
    ]
    if bgm_segments:
        tracks.append({"id": _uid(), "type": "audio", "attribute": 0, "segments": bgm_segments})
    if text_segments:
        tracks.append({"id": _uid(), "type": "text", "attribute": 0, "segments": text_segments})

    draft_id = _uid()
    now_us = int(time.time() * US)
    draft_content = {
        "id": draft_id,
        "version": 360000,
        "name": plan.title,
        "duration": _us(plan.total_duration),
        "fps": plan.fps,
        "canvas_config": {
            "width": plan.width,
            "height": plan.height,
            "ratio": "16:9" if plan.width >= plan.height else "9:16",
        },
        "materials": {
            "videos": video_materials,
            "audios": audio_materials,
            "texts": text_materials,
            "speeds": speed_materials,
        },
        "tracks": tracks,
    }
    draft_meta = {
        "draft_id": draft_id,
        "draft_name": plan.title,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": _us(plan.total_duration),
    }

    (draft_dir / "draft_content.json").write_text(
        json.dumps(draft_content, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(draft_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "README_JIANYING.txt").write_text(
        "本草稿仅包含剪映免费基础轨（视频、音频、基础纯文本字幕），不含任何 VIP 特效/花字/贴纸。\n"
        "注意：剪映 6.0+ 桌面端在保存后会加密 draft_content.json；若用于长期免会员微调，\n"
        "更推荐使用同目录的 timeline.otio / timeline.fcpxml + subtitles.srt 导入【达芬奇免费版】。\n",
        encoding="utf-8",
    )
    return draft_dir
