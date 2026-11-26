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
    assert plan.width == 1080 and plan.height == 1920
    assert paths.fine_preview_mp4.exists()
    assert paths.timeline_otio.exists()
    assert paths.timeline_fcpxml.exists()
    assert paths.qc_contact_jpg.exists()


def test_mcp_server_initializes_tools() -> None:
    server = create_server()
    assert server.name == "castcut"
