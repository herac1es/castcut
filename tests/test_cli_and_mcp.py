"""Tests for portrait auto-detection, CLI invocation, and MCP server registration."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from castcut.cli import app
from castcut.mcp_server import create_server
from castcut.models import AspectMode, MixedOrientationPolicy, TemplateType
from castcut.workspace import EpisodePaths, load_edit_plan
from tests.test_pipeline import _make_synthetic_clip

runner = CliRunner()


def test_cli_doctor_and_portrait_auto_workflow(tmp_path: Path) -> None:
    doc_res = runner.invoke(app, ["doctor"])
    assert doc_res.exit_code == 0, doc_res.output
    assert "FFmpeg" in doc_res.output

    ep_dir = tmp_path / "ep_portrait_short"
    init_res = runner.invoke(
        app,
        [
            "init",
            str(ep_dir),
            "--title",
            "竖屏爆口短视频",
            "--aspect",
            AspectMode.AUTO.value,
            "--mixed",
            MixedOrientationPolicy.BLUR_PILLARBOX.value,
            "--template",
            TemplateType.SHORT_HIGHLIGHT.value,
        ],
    )
    assert init_res.exit_code == 0, init_res.output

    # Portrait footage dominates (7s portrait vs 3s landscape)
    _make_synthetic_clip(ep_dir / "raw" / "01_vertical_catch.mp4", 720, 1280, 7.0, "0x264653", 580, volume=0.6)
    _make_synthetic_clip(ep_dir / "raw" / "02_landscape_broll.mp4", 1280, 720, 3.0, "0x2a9d8f", 300, volume=0.2)

    run_res = runner.invoke(app, ["run", str(ep_dir), "--asr", "none", "--formats", "otio,fcpxml,srt"])
    assert run_res.exit_code == 0, run_res.output

    paths = EpisodePaths(ep_dir)
    plan = load_edit_plan(paths.fine_cut_yaml)
    # Portrait aligns with coolbot 3:4 (1080x1440) to avoid top/bottom feed occlusion
    assert plan.width == 1080 and plan.height == 1440
    # 9:16 vertical clip (720x1280) entering 3:4 canvas must use blur_pillarbox (no black bars!)
    assert all(it.framing == MixedOrientationPolicy.BLUR_PILLARBOX for it in plan.items)
    assert any(ov.kind == "watermark" and ov.title == "@路亚知马力" for ov in plan.overlays)
    assert paths.fine_preview_mp4.exists()
    assert paths.timeline_otio.exists()
    assert paths.timeline_fcpxml.exists()
    assert paths.qc_contact_jpg.exists()
    assert "V3 Channel Handle" in paths.timeline_otio.read_text(encoding="utf-8")


def test_aspect_aliases_and_4k_davinci_ceiling() -> None:
    from castcut.models import AspectMode, ProjectConfig, ResolutionTier, parse_aspect_mode

    assert parse_aspect_mode("3:4") == AspectMode.PORTRAIT
    assert parse_aspect_mode("16:9") == AspectMode.LANDSCAPE
    cfg_4k = ProjectConfig(
        episode_id="ep", title="t", aspect="3:4", resolution=ResolutionTier.UHD_4K
    )
    assert cfg_4k.resolve_dimensions("portrait") == (1620, 2160)


def test_mcp_server_initializes_tools() -> None:
    server = create_server()
    assert server.name == "castcut"


def test_renderer_imports_cleanly_without_circular_error() -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "import castcut.renderer, castcut.exporters"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
