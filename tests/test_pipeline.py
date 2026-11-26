"""Unit and full synthetic mixed-orientation E2E tests for CastCut."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from castcut.exporters import export_episode
from castcut.exporters.srt import format_srt_time
from castcut.glossary import load_glossary
from castcut.ingest import ingest_episode
from castcut.media import resolve_ffmpeg
from castcut.models import AspectMode, ClipCategory, MixedOrientationPolicy, TemplateType
from castcut.planner import build_fine_plan, build_rough_plan
from castcut.qc import run_qc
from castcut.renderer import build_atempo_chain, render_episode
from castcut.workspace import (
    EpisodePaths,
    init_episode_workspace,
    load_clip_catalog,
    save_clip_catalog,
)


def _make_synthetic_clip(
    dest: Path,
    width: int,
    height: int,
    duration: float,
    color: str,
    freq_hz: int,
    volume: float = 0.25,
) -> None:
    ffmpeg = resolve_ffmpeg().path
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:r=30:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq_hz}:sample_rate=48000:duration={duration}",
        "-af",
        f"volume={volume}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def _make_synthetic_bgm(dest: Path, duration: float = 12.0) -> None:
    ffmpeg = resolve_ffmpeg().path
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=48000:duration={duration}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_glossary_corrections_and_keywords() -> None:
    g = load_glossary()
    fixed = g.correct_text("今天在表点用迷诺和千头钩中鱼了，还差点炸现跑余")
    assert "标点" in fixed
    assert "米诺" in fixed
    assert "铅头钩" in fixed
    assert "炸线" in fixed
    assert "跑鱼" in fixed

    kws, cat = g.match_keywords(fixed)
    assert {"中鱼", "炸线", "跑鱼"}.issubset(set(kws))
    assert cat == ClipCategory.BLOOPER

    kws2, cat2 = g.match_keywords(g.correct_text("咬了！次鱼！柳鱼抄网上岸！"))
    assert "刺鱼" in kws2 and "溜鱼" in kws2
    assert cat2 == ClipCategory.FIGHT_CATCH


def test_atempo_chain_and_srt_time() -> None:
    assert build_atempo_chain(1.0) == "anull"
    assert build_atempo_chain(4.0) == "atempo=2.00000,atempo=2.00000"
    assert build_atempo_chain(0.25) == "atempo=0.50000,atempo=0.50000"
    assert format_srt_time(3661.25) == "01:01:01,250"


def test_full_mixed_orientation_lure_vlog_e2e(tmp_path: Path) -> None:
    ep_dir = tmp_path / "ep_spring_bass"
    bgm_file = tmp_path / "bgm.m4a"
    _make_synthetic_bgm(bgm_file, duration=10.0)

    init_episode_workspace(
        episode_dir=ep_dir,
        title="早春管理场路亚测试",
        aspect=AspectMode.AUTO,
        mixed_orientation=MixedOrientationPolicy.BLUR_PILLARBOX,
        template=TemplateType.FISHING_VLOG,
        target_duration_sec=60.0,
        location="近郊管理场",
        bgm_path=str(bgm_file),
    )

    raw_dir = ep_dir / "raw"
    # 1. Landscape talking clip (1280x720, 5s) + sidecar SRT with intentional lure typos
    _make_synthetic_clip(raw_dir / "01_intro_spot.mp4", 1280, 720, 5.0, "0x1d4e89", 440)
    (raw_dir / "01_intro_spot.srt").write_text(
        "1\n00:00:00,200 --> 00:00:04,500\n今天来这个表点，试试迷诺搜一下浅滩结构。\n",
        encoding="utf-8",
    )

    # 2. Historical portrait clip (720x1280, 4s) — gear/rig closeup to test mixed orientation!
    _make_synthetic_clip(raw_dir / "02_gear_lure_vertical.mp4", 720, 1280, 4.0, "0x2a9d8f", 520)
    (raw_dir / "02_gear_lure_vertical.srt").write_text(
        "1\n00:00:00,300 --> 00:00:03,500\n用的是五克千头钩配软重，防挂又自然。\n",
        encoding="utf-8",
    )

    # 3. Landscape catch highlight (1280x720, 6s, louder audio)
    _make_synthetic_clip(raw_dir / "03_catch_bass.mp4", 1280, 720, 6.0, "0xe76f51", 660, volume=0.65)
    (raw_dir / "03_catch_bass.srt").write_text(
        "1\n00:00:00,500 --> 00:00:05,200\n咬了！次鱼！漂亮的一条大口黑炉，柳鱼抄网上岸！\n",
        encoding="utf-8",
    )

    # 4. Landscape blooper clip (1280x720, 3.5s)
    _make_synthetic_clip(raw_dir / "04_blooper_snag.mp4", 1280, 720, 3.5, "0xf4a261", 330)
    (raw_dir / "04_blooper_snag.srt").write_text(
        "1\n00:00:00,300 --> 00:00:03,000\n哎呀挂地了还炸现，纯纯翻车现场。\n",
        encoding="utf-8",
    )

    # Stage 1: Ingest
    media_index, catalog = ingest_episode(ep_dir, asr_mode="none", make_proxies=True, make_contacts=True)
    assert len(media_index.files) == 4
    assert media_index.dominant_orientation == "landscape"
    assert media_index.portrait_duration == 4.0
    assert media_index.landscape_duration == 14.5
    assert all(m.proxy_rel_path and (ep_dir / m.proxy_rel_path).exists() for m in media_index.files)
    assert all(m.contact_sheet_rel_path and (ep_dir / m.contact_sheet_rel_path).exists() for m in media_index.files)

    # Check glossary correction applied during SRT import
    all_text = " ".join(c.transcript_text for c in catalog.clips)
    assert "标点" in all_text and "米诺" in all_text and "铅头钩" in all_text and "刺鱼" in all_text and "炸线" in all_text

    # Confirm vertical clip detected accurately
    vertical_clips = [c for c in catalog.clips if c.orientation == "portrait"]
    assert len(vertical_clips) == 1
    assert vertical_clips[0].category == ClipCategory.GEAR_RIG

    # Simulate Agent refining one clip label
    reloaded = load_clip_catalog(EpisodePaths(ep_dir).clips_json)
    reloaded.clips[0].agent_note = "开场标点介绍"
    save_clip_catalog(EpisodePaths(ep_dir).clips_json, reloaded)

    # Stage 2: Rough cut plan + proxy preview render
    rough_plan = build_rough_plan(ep_dir)
    assert rough_plan.width == 1920 and rough_plan.height == 1080
    assert rough_plan.items[0].section == "hook"
    portrait_items = [it for it in rough_plan.items if it.orientation == "portrait"]
    assert portrait_items and portrait_items[0].framing == MixedOrientationPolicy.BLUR_PILLARBOX

    rough_mp4 = render_episode(ep_dir, stage="rough")
    assert rough_mp4.exists() and rough_mp4.stat().st_size > 10_000

    # Stage 3: Fine cut plan + full render + all exporters
    fine_plan = build_fine_plan(ep_dir)
    assert len(fine_plan.subtitles) >= 3
    assert len(fine_plan.overlays) >= 2
    assert fine_plan.bgm is not None and len(fine_plan.bgm.ducking_intervals) >= 1

    fine_mp4 = render_episode(ep_dir, stage="fine")
    assert fine_mp4.exists() and fine_mp4.stat().st_size > 20_000

    exported = export_episode(ep_dir, formats=["otio", "fcpxml", "srt", "jianying"])
    assert set(exported.keys()) == {"otio", "fcpxml", "srt", "jianying"}

    # Validate OTIO JSON schema structure
    otio_data = json.loads(exported["otio"].read_text(encoding="utf-8"))
    assert otio_data["OTIO_SCHEMA"] == "Timeline.1"
    track_names = [t["name"] for t in otio_data["tracks"]["children"]]
    assert "V1 Main" in track_names
    assert "V2 HUD Overlays" in track_names
    assert "A1 Dialogue & Field Audio" in track_names
    assert "A2 BGM" in track_names

    # Validate FCPXML 1.10 XML parses cleanly & contains assets + spine clips + markers
    fcpxml_root = ET.fromstring(exported["fcpxml"].read_text(encoding="utf-8"))
    assert fcpxml_root.tag == "fcpxml" and fcpxml_root.attrib["version"] == "1.10"
    assert len(fcpxml_root.findall(".//spine/asset-clip")) == len(fine_plan.items)
    assert len(fcpxml_root.findall(".//marker")) >= 1

    # Validate SRT content
    srt_text = exported["srt"].read_text(encoding="utf-8")
    assert "大口黑鲈" in srt_text and "-->" in srt_text

    # Validate zero-VIP Jianying draft
    jy_content = json.loads((exported["jianying"] / "draft_content.json").read_text(encoding="utf-8"))
    assert jy_content["canvas_config"]["width"] == 1920
    assert len(jy_content["tracks"]) >= 2

    # Stage 4: QC Gate
    qc_report = run_qc(ep_dir, stage="fine")
    assert qc_report.passed is True
    assert qc_report.qc_contact_sheet is not None
    assert (ep_dir / qc_report.qc_contact_sheet).exists()
