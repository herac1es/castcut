"""Pluggable ASR transcription: sidecar SRT/JSON import + optional local faster-whisper."""

from __future__ import annotations

import json
import re
from pathlib import Path

from castcut.glossary import LureGlossary
from castcut.models import MediaTranscript, TranscriptSegment, TranscriptWord

LURE_WHISPER_PROMPT = (
    "路亚钓鱼Vlog，常见词：大口黑鲈、加州鲈、鳜鱼、翘嘴、黑鱼、米诺、波爬、铅头钩、"
    "德州钓组、无铅、倒钓、软虫、复合亮片、VIB、水滴轮、纺车轮、碳线、PE线、前导、"
    "标点、结构、刺鱼、溜鱼、洗鳃、抄网、放流、挂底、炸线、炒粉、跑鱼、爆口、空军。"
)


def _parse_srt_ts(ts: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        return 0.0
    h, mn, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return round(h * 3600 + mn * 60 + s + ms / 1000.0, 3)


def load_sidecar_srt(srt_path: Path, media_id: str, glossary: LureGlossary) -> MediaTranscript:
    text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    segments: list[TranscriptSegment] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[ts_idx]:
            continue
        start_s, end_s = lines[ts_idx].split("-->", 1)
        raw_txt = " ".join(lines[ts_idx + 1 :]).strip()
        if not raw_txt:
            continue
        fixed = glossary.correct_text(raw_txt)
        segments.append(
            TranscriptSegment(
                start=_parse_srt_ts(start_s),
                end=_parse_srt_ts(end_s),
                text=fixed,
                raw_text=raw_txt if fixed != raw_txt else None,
            )
        )
    return MediaTranscript(media_id=media_id, source="sidecar-srt", segments=segments)


def load_sidecar_json(json_path: Path, media_id: str, glossary: LureGlossary) -> MediaTranscript:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    raw_segments = data.get("segments") if isinstance(data, dict) else data
    segments: list[TranscriptSegment] = []
    for seg in raw_segments or []:
        raw_txt = str(seg.get("text") or "").strip()
        if not raw_txt:
            continue
        fixed = glossary.correct_text(raw_txt)
        words = [
            TranscriptWord(
                start=round(float(w.get("start", 0.0)), 3),
                end=round(float(w.get("end", 0.0)), 3),
                word=str(w.get("word", "")),
                probability=w.get("probability"),
            )
            for w in (seg.get("words") or [])
        ]
        segments.append(
            TranscriptSegment(
                start=round(float(seg.get("start", 0.0)), 3),
                end=round(float(seg.get("end", 0.0)), 3),
                text=fixed,
                raw_text=raw_txt if fixed != raw_txt else None,
                words=words,
            )
        )
    return MediaTranscript(media_id=media_id, source="sidecar-json", segments=segments)


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def transcribe_media(
    video_path: Path,
    media_id: str,
    glossary: LureGlossary,
    mode: str = "auto",
    model_size: str = "small",
    language: str = "zh",
) -> MediaTranscript:
    """Resolve transcript via sidecar SRT/JSON first, then optional faster-whisper."""
    # Always honor explicit sidecar transcript matching video stem (e.g. from MacWhisper/Whisper.cpp).
    srt_sidecar = video_path.with_suffix(".srt")
    if srt_sidecar.exists():
        return load_sidecar_srt(srt_sidecar, media_id, glossary)
    json_sidecar = video_path.with_suffix(".transcript.json")
    if json_sidecar.exists():
        return load_sidecar_json(json_sidecar, media_id, glossary)

    if mode == "none":
        return MediaTranscript(media_id=media_id, source="none", language=language, segments=[])

    if not faster_whisper_available():
        if mode == "faster-whisper":
            raise RuntimeError(
                "faster-whisper is not installed. Run `uv sync --extra asr` or place .srt sidecars in raw/."
            )
        return MediaTranscript(media_id=media_id, source="none", language=language, segments=[])

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="int8")
    raw_segs, info = model.transcribe(
        str(video_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=LURE_WHISPER_PROMPT,
    )
    segments: list[TranscriptSegment] = []
    for seg in raw_segs:
        raw_txt = (seg.text or "").strip()
        if not raw_txt:
            continue
        fixed = glossary.correct_text(raw_txt)
        words = [
            TranscriptWord(
                start=round(float(w.start), 3),
                end=round(float(w.end), 3),
                word=w.word,
                probability=round(float(w.probability), 3) if w.probability is not None else None,
            )
            for w in (seg.words or [])
        ]
        segments.append(
            TranscriptSegment(
                start=round(float(seg.start), 3),
                end=round(float(seg.end), 3),
                text=fixed,
                raw_text=raw_txt if fixed != raw_txt else None,
                words=words,
            )
        )
    return MediaTranscript(
        media_id=media_id,
        source="faster-whisper",
        language=getattr(info, "language", language) or language,
        segments=segments,
    )
