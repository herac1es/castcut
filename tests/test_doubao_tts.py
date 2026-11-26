"""Unit and E2E tests for Volcengine Doubao TTS integration, caching, polyphones, and NLE tracks."""

from __future__ import annotations

import base64
import json
import subprocess
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from castcut.exporters import export_episode
from castcut.ingest import ingest_episode
from castcut.media import resolve_ffmpeg
from castcut.models import AspectMode, MixedOrientationPolicy, TemplateType, VoiceoverCue
from castcut.planner import build_fine_plan, build_rough_plan
from castcut.qc import run_qc
from castcut.renderer import render_episode
from castcut.tts import (
    DEFAULT_VOLCENGINE_TTS_VOICE,
    DoubaoTtsClient,
    DoubaoTtsConfig,
    apply_polyphone_aliases,
    load_doubao_tts_config,
    restore_polyphone_text,
)
from castcut.voiceover import synthesize_episode_voiceovers
from castcut.workspace import EpisodePaths, init_episode_workspace, save_edit_plan
from tests.test_pipeline import _make_synthetic_bgm, _make_synthetic_clip


def _make_valid_mp3_bytes(tmp_path: Path, duration: float = 1.6, freq: int = 500) -> bytes:
    mp3 = tmp_path / f"tone_{freq}.mp3"
    ff = resolve_ffmpeg().path
    proc = subprocess.run(
        [
            ff,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=24000:duration={duration}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(mp3),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return mp3.read_bytes()


class _MockVolcServer:
    def __init__(self, handler_fn):
        self.calls: list[tuple[dict[str, str], dict]] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(inner_self):
                length = int(inner_self.headers.get("Content-Length") or "0")
                raw = inner_self.rfile.read(length).decode("utf-8")
                body = json.loads(raw) if raw else {}
                headers = {k.lower(): v for k, v in inner_self.headers.items()}
                self.calls.append((headers, body))
                status, resp_headers, resp_text = handler_fn(headers, body)
                inner_self.send_response(status)
                for k, v in resp_headers.items():
                    inner_self.send_header(k, v)
                inner_self.end_headers()
                inner_self.wfile.write(resp_text.encode("utf-8"))

            def log_message(inner_self, fmt, *args):
                return

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/api/v3/tts/unidirectional"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()


def test_polyphone_disambiguation_matches_coolbot() -> None:
    src = "后面的F是调性，快调主要弯竿梢，中快调卸力，但微调旋钮先调高两档。"
    tts_text, has_diao = apply_polyphone_aliases(src)
    assert tts_text == "后面的F是吊性，快吊主要弯竿梢，中快吊卸力，但微调旋钮先调高两档。"
    assert not has_diao
    assert restore_polyphone_text(tts_text, has_diao) == src

    # Authentic '吊' preserved alongside rod-action '调'
    mixed = "倒钓吊着晃，快调才好使。"
    m_tts, m_has = apply_polyphone_aliases(mixed)
    assert m_tts == "倒钓吊着晃，快吊才好使。"
    assert restore_polyphone_text(m_tts, m_has) == mixed


def test_doubao_v2_auth_word_subtitles_and_cache(tmp_path: Path) -> None:
    mp3_bytes = _make_valid_mp3_bytes(tmp_path, duration=1.8, freq=520)
    half = len(mp3_bytes) // 2
    full_text = "泛用套装别急着上三本钩，换用3.5g精细软虫慢磨探路。"
    words = [
        {"word": ch, "startTime": round(i * 0.06, 2), "endTime": round((i + 1) * 0.06, 2)}
        for i, ch in enumerate(full_text)
    ]

    def handler(_headers, _body):
        lines = [
            json.dumps({"code": 0, "message": "", "data": base64.b64encode(mp3_bytes[:half]).decode()}),
            json.dumps({"code": 0, "message": "", "data": None, "sentence": {"text": full_text, "words": words}}),
            json.dumps({"code": 0, "message": "", "data": base64.b64encode(mp3_bytes[half:]).decode()}),
            json.dumps({"code": 20000000, "message": "ok", "data": None, "usage": {"text_words": 25}}),
        ]
        return 200, {"Content-Type": "application/json", "X-Tt-Logid": "mock-log-200"}, "\n".join(lines) + "\n"

    with _MockVolcServer(handler) as srv:
        cfg = DoubaoTtsConfig(
            endpoint=srv.url,
            api_key="test-key-v3",
            resource_id="seed-tts-2.0",
            voice=DEFAULT_VOLCENGINE_TTS_VOICE,
            speech_rate=10,
            loudness_rate=5,
            context_text="语气轻松自然，像老钓友闲聊",
        )
        cache_dir = tmp_path / "cache"
        client = DoubaoTtsClient(cfg, cache_dir=cache_dir)

        out1 = tmp_path / "vo1.mp3"
        res1 = client.synthesize(full_text, out1)
        assert not res1.from_cache
        assert out1.read_bytes() == mp3_bytes
        assert res1.duration_sec >= 1.5
        assert len(res1.subtitles) == 2
        assert res1.subtitles[0].text == "泛用套装别急着上三本钩"
        assert res1.subtitles[1].text == "换用3.5g精细软虫慢磨探路"
        assert len(srv.calls) == 1

        # Verify request contract mirrors coolbot
        hdrs, body = srv.calls[0]
        assert hdrs["x-api-key"] == "test-key-v3"
        assert hdrs["x-api-resource-id"] == "seed-tts-2.0"
        assert hdrs["x-control-require-usage-tokens-return"] == "text_words"
        assert body["req_params"]["audio_params"]["enable_subtitle"] is True
        assert "enable_timestamp" not in body["req_params"]["audio_params"]
        additions = json.loads(body["req_params"]["additions"])
        assert additions["disable_markdown_filter"] is True
        assert additions["context_texts"] == ["语气轻松自然，像老钓友闲聊"]

        # Second identical synthesis hits local cache with 0 additional API requests
        out2 = tmp_path / "vo2.mp3"
        res2 = client.synthesize(full_text, out2)
        assert res2.from_cache
        assert out2.read_bytes() == mp3_bytes
        assert len(srv.calls) == 1


def test_doubao_legacy_v1_auth_and_error_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mp3_bytes = _make_valid_mp3_bytes(tmp_path, duration=1.2, freq=440)

    def ok_v1(_headers, _body):
        lines = [
            json.dumps({"code": 0, "message": "", "data": base64.b64encode(mp3_bytes).decode()}),
            json.dumps({"code": 20000000, "message": "ok", "data": None}),
        ]
        return 200, {"Content-Type": "application/json"}, "\n".join(lines) + "\n"

    with _MockVolcServer(ok_v1) as srv:
        cfg = DoubaoTtsConfig(
            endpoint=srv.url,
            app_id="legacy-app",
            access_key="legacy-secret",
            resource_id="seed-tts-1.0",
            voice="zh_male_jieshuoxiaoming_moon_bigtts",
            context_text="ignored for v1",
        )
        client = DoubaoTtsClient(cfg)
        client.synthesize("测试旧版鉴权。", tmp_path / "v1.mp3")
        hdrs, body = srv.calls[0]
        assert "x-api-key" not in hdrs
        assert hdrs["x-api-app-id"] == "legacy-app"
        assert hdrs["x-api-access-key"] == "legacy-secret"
        assert body["req_params"]["audio_params"]["enable_timestamp"] is True
        assert "context_texts" not in json.loads(body["req_params"]["additions"])

    def err_450(_headers, _body):
        return (
            200,
            {"Content-Type": "application/json", "X-Tt-Logid": "log-450-err"},
            json.dumps({"code": 45000000, "message": "speaker permission denied", "data": None}) + "\n",
        )

    with _MockVolcServer(err_450) as srv:
        client = DoubaoTtsClient(DoubaoTtsConfig(endpoint=srv.url, api_key="k"))
        with pytest.raises(RuntimeError, match="45000000.*log-450-err.*VOLCENGINE_TTS_RESOURCE_ID"):
            client.synthesize("错误测试", tmp_path / "err.mp3")

    monkeypatch.setenv("VOLCENGINE_TTS_SPEECH_RATE", "999")
    with pytest.raises(ValueError, match="VOLCENGINE_TTS_SPEECH_RATE"):
        load_doubao_tts_config(load_env_file=False)


def test_episode_doubao_voiceover_e2e_render_and_davinci_export(tmp_path: Path) -> None:
    mp3_bytes = _make_valid_mp3_bytes(tmp_path, duration=1.5, freq=580)
    ep_dir = tmp_path / "ep_vo_vlog"
    bgm = tmp_path / "bgm.m4a"
    _make_synthetic_bgm(bgm, duration=10.0)

    init_episode_workspace(
        ep_dir,
        title="豆包配音实战测试",
        aspect=AspectMode.PORTRAIT,
        mixed_orientation=MixedOrientationPolicy.BLUR_PILLARBOX,
        template=TemplateType.SHORT_HIGHLIGHT,
        bgm_path=str(bgm),
    )
    _make_synthetic_clip(ep_dir / "raw" / "01_catch.mp4", 720, 1280, 5.0, "0x1d4e89", 360)
    _make_synthetic_clip(ep_dir / "raw" / "02_spot.mp4", 1280, 720, 4.0, "0x2a9d8f", 320)

    ingest_episode(ep_dir, asr_mode="none", make_proxies=False, make_contacts=False)
    build_rough_plan(ep_dir)
    fine = build_fine_plan(ep_dir)

    # Add both clip-bound voiceover_text AND standalone timed VoiceoverCue
    fine.items[0].voiceover_text = "开局直接爆口，快调竿瞬间弯弓！"
    fine.voiceovers.append(
        VoiceoverCue(vo_id="VO001", text="这个标点用3.5g铅头钩贴底慢磨。", start=3.0)
    )
    save_edit_plan(EpisodePaths(ep_dir).fine_cut_yaml, fine)

    def mock_tts(_headers, body):
        txt = body["req_params"]["text"]
        words = [
            {"word": ch, "startTime": round(i * 0.08, 2), "endTime": round((i + 1) * 0.08, 2)}
            for i, ch in enumerate(txt)
        ]
        lines = [
            json.dumps({"code": 0, "message": "", "data": base64.b64encode(mp3_bytes).decode()}),
            json.dumps({"code": 0, "message": "", "data": None, "sentence": {"text": txt, "words": words}}),
            json.dumps({"code": 20000000, "message": "ok", "data": None}),
        ]
        return 200, {"Content-Type": "application/json"}, "\n".join(lines) + "\n"

    with _MockVolcServer(mock_tts) as srv:
        client = DoubaoTtsClient(
            DoubaoTtsConfig(endpoint=srv.url, api_key="mock-key"),
            cache_dir=EpisodePaths(ep_dir).tts_cache_dir,
        )
        updated, count, hits = synthesize_episode_voiceovers(ep_dir, client=client)
        assert count == 2 and hits == 0
        assert all(v.audio_rel_path and (ep_dir / v.audio_rel_path).exists() for v in updated.voiceovers)
        # Polyphone '快调' must be restored cleanly in subtitles
        srt_text = EpisodePaths(ep_dir).subtitles_srt.read_text(encoding="utf-8")
        assert "快调竿瞬间弯弓" in srt_text and "吊" not in srt_text
        assert "3.5g铅头钩" in srt_text

    # Render fine preview mixing ducked field audio + Doubao TTS + ducked BGM
    preview = render_episode(ep_dir, stage="fine")
    assert preview.exists() and preview.stat().st_size > 15_000

    # Export DaVinci OTIO + FCPXML with dedicated Voiceover track A2 & BGM track A3
    exported = export_episode(ep_dir, formats=["otio", "fcpxml", "srt", "jianying"])
    otio_data = json.loads(exported["otio"].read_text(encoding="utf-8"))
    track_names = [t["name"] for t in otio_data["tracks"]["children"]]
    assert "A1 Dialogue & Field Audio" in track_names
    assert "A2 Voiceover TTS" in track_names
    assert "A3 BGM" in track_names

    fcpxml_root = ET.fromstring(exported["fcpxml"].read_text(encoding="utf-8"))
    vo_clips = [el for el in fcpxml_root.findall(".//asset-clip") if el.attrib.get("role") == "dialogue.voiceover"]
    bgm_clips = [el for el in fcpxml_root.findall(".//asset-clip") if el.attrib.get("role") == "music"]
    assert len(vo_clips) == 2
    assert len(bgm_clips) == 1 and bgm_clips[0].attrib.get("lane") == "-2"

    qc = run_qc(ep_dir, stage="fine")
    assert qc.passed is True
