"""UTF-8 SRT subtitle exporter."""

from __future__ import annotations

from pathlib import Path

from castcut.models import SubtitleCue


def format_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[SubtitleCue], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{format_srt_time(cue.start)} --> {format_srt_time(cue.end)}")
        lines.append(cue.text.strip())
        lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest
