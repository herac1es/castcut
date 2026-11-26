"""Cross-platform FFmpeg/FFprobe resolution, media probing, analysis, proxies, and contact sheets."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from castcut.models import AudioPeak, MediaFile, SilenceInterval

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".mts", ".avi", ".webm", ".ts"}
TEN_BIT_PIX_FMTS = (
    "10le",
    "10be",
    "p010",
    "yuv420p10",
    "yuv422p10",
    "yuv444p10",
    "gbrp10",
)


@dataclass(frozen=True)
class BinaryInfo:
    path: str
    version: str
    major: int
    source: str


def _parse_ffmpeg_major(version_line: str) -> tuple[str, int]:
    m = re.search(r"version\s+([nN]?\d+(?:\.\d+)*)", version_line)
    if not m:
        return version_line.strip(), 0
    ver = m.group(1).lstrip("nN")
    try:
        major = int(ver.split(".")[0])
    except ValueError:
        major = 0
    return ver, major


def _probe_binary_version(binary_path: str) -> tuple[str, int]:
    try:
        proc = subprocess.run(
            [binary_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        first_line = (proc.stdout or proc.stderr or "").splitlines()
        if not first_line:
            return "unknown", 0
        return _parse_ffmpeg_major(first_line[0])
    except Exception:
        return "unknown", 0


def resolve_ffmpeg(min_major: int = 5) -> BinaryInfo:
    """Resolve a modern FFmpeg binary (env -> system >= min_major -> imageio-ffmpeg -> system fallback)."""
    env_bin = os.environ.get("CASTCUT_FFMPEG")
    if env_bin and Path(env_bin).exists():
        ver, major = _probe_binary_version(env_bin)
        return BinaryInfo(env_bin, ver, major, "env:CASTCUT_FFMPEG")

    sys_bin = shutil.which("ffmpeg")
    sys_info: BinaryInfo | None = None
    if sys_bin:
        ver, major = _probe_binary_version(sys_bin)
        sys_info = BinaryInfo(sys_bin, ver, major, "system")
        if major >= min_major:
            return sys_info

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            ver, major = _probe_binary_version(bundled)
            return BinaryInfo(bundled, ver, major, "imageio-ffmpeg")
    except Exception:
        pass

    if sys_info is not None:
        return sys_info
    raise RuntimeError(
        "FFmpeg not found. Install FFmpeg (macOS: `brew install ffmpeg`, Windows: `winget install Gyan.FFmpeg`) "
        "or ensure `imageio-ffmpeg` is installed."
    )


def resolve_ffprobe() -> BinaryInfo | None:
    """Resolve ffprobe binary if installed; returns None if unavailable (caller can fallback to ffmpeg -i)."""
    env_bin = os.environ.get("CASTCUT_FFPROBE")
    if env_bin and Path(env_bin).exists():
        ver, major = _probe_binary_version(env_bin)
        return BinaryInfo(env_bin, ver, major, "env:CASTCUT_FFPROBE")

    sys_bin = shutil.which("ffprobe")
    if sys_bin:
        ver, major = _probe_binary_version(sys_bin)
        return BinaryInfo(sys_bin, ver, major, "system")
    return None


def _parse_fps(rate_str: str | None) -> float:
    if not rate_str or rate_str in ("0/0", "N/A"):
        return 30.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        try:
            d = float(den)
            return round(float(num) / d, 3) if d > 0 else 30.0
        except ValueError:
            return 30.0
    try:
        return round(float(rate_str), 3)
    except ValueError:
        return 30.0


def _extract_rotation(video_stream: dict) -> int:
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except ValueError:
            pass
    for sd in video_stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(float(sd["rotation"])) % 360
            except ValueError:
                pass
    return 0


def _probe_via_ffmpeg_stderr(path: Path) -> dict:
    """Fallback probe when ffprobe is not available on PATH."""
    ffmpeg = resolve_ffmpeg().path
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    err = proc.stderr or ""
    dur = 0.0
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if dm:
        dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
    width, height, fps, vcodec, pix_fmt = 1920, 1080, 30.0, "", ""
    vm = re.search(r"Stream #.*Video:\s*([^,\s]+)[^,]*,\s*([^,\s(]+).*?,\s*(\d{2,5})x(\d{2,5})", err)
    if vm:
        vcodec = vm.group(1)
        pix_fmt = vm.group(2)
        width = int(vm.group(3))
        height = int(vm.group(4))
    fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    if fm:
        fps = float(fm.group(1))
    rotation = 0
    rm = re.search(r"displaymatrix:\s*rotation of\s*(-?\d+(?:\.\d+)?)", err)
    if rm:
        rotation = int(round(float(rm.group(1)))) % 360
    am = re.search(r"Stream #.*Audio:\s*([^,\s]+)", err)
    has_audio = am is not None
    acodec = am.group(1) if am else ""
    return {
        "duration": dur,
        "width": width,
        "height": height,
        "rotation": rotation,
        "fps": fps,
        "video_codec": vcodec,
        "audio_codec": acodec,
        "has_audio": has_audio,
        "pix_fmt": pix_fmt,
        "creation_time": None,
    }


def probe_video(path: Path, media_id: str, rel_path: str) -> MediaFile:
    """Probe full technical metadata for a video file, respecting rotation & 10-bit flags."""
    ffprobe = resolve_ffprobe()
    meta: dict | None = None
    if ffprobe is not None:
        proc = subprocess.run(
            [
                ffprobe.path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            streams = data.get("streams") or []
            fmt = data.get("format") or {}
            vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
            astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            if vstream is not None:
                duration = float(fmt.get("duration") or vstream.get("duration") or 0.0)
                rotation = _extract_rotation(vstream)
                raw_w = int(vstream.get("width") or 1920)
                raw_h = int(vstream.get("height") or 1080)
                fps = _parse_fps(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate"))
                tags = fmt.get("tags") or vstream.get("tags") or {}
                meta = {
                    "duration": duration,
                    "width": raw_w,
                    "height": raw_h,
                    "rotation": rotation,
                    "fps": fps,
                    "video_codec": str(vstream.get("codec_name") or ""),
                    "audio_codec": str(astream.get("codec_name") or "") if astream else "",
                    "has_audio": astream is not None,
                    "pix_fmt": str(vstream.get("pix_fmt") or ""),
                    "creation_time": tags.get("creation_time"),
                }

    if meta is None:
        meta = _probe_via_ffmpeg_stderr(path)

    eff_w, eff_h = meta["width"], meta["height"]
    if meta["rotation"] in (90, 270):
        eff_w, eff_h = eff_h, eff_w

    if eff_w > eff_h:
        orientation = "landscape"
    elif eff_h > eff_w:
        orientation = "portrait"
    else:
        orientation = "square"

    pix_fmt = meta["pix_fmt"]
    is_10bit = any(token in pix_fmt.lower() for token in TEN_BIT_PIX_FMTS)
    warning = None
    if is_10bit:
        warning = (
            f"检测到 10-bit 素材 ({pix_fmt})：Windows 达芬奇免费版可能无法直接硬解，"
            "建议导出前运行 `castcut prepare-davinci` 转成 8-bit H.264 或 ProRes。"
        )

    return MediaFile(
        media_id=media_id,
        filename=path.name,
        rel_path=rel_path,
        abs_path=str(path.resolve()),
        duration=round(float(meta["duration"]), 3),
        width=eff_w,
        height=eff_h,
        rotation=meta["rotation"],
        orientation=orientation,
        fps=meta["fps"],
        video_codec=meta["video_codec"],
        audio_codec=meta["audio_codec"],
        has_audio=bool(meta["has_audio"]),
        pix_fmt=pix_fmt,
        is_10bit=is_10bit,
        file_size_bytes=path.stat().st_size if path.exists() else 0,
        creation_time=meta["creation_time"],
        davinci_free_warning=warning,
    )


def detect_silences(
    path: Path,
    noise_db: float = -34.0,
    min_silence_sec: float = 0.7,
    total_duration: float | None = None,
) -> list[SilenceInterval]:
    """Detect quiet intervals using FFmpeg silencedetect."""
    ffmpeg = resolve_ffmpeg().path
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    silences: list[SilenceInterval] = []
    current_start: float | None = None
    for line in (proc.stderr or "").splitlines():
        sm = re.search(r"silence_start:\s*(-?\d+(?:\.\d+)?)", line)
        if sm:
            current_start = max(0.0, float(sm.group(1)))
            continue
        em = re.search(r"silence_end:\s*(-?\d+(?:\.\d+)?)", line)
        if em and current_start is not None:
            end = float(em.group(1))
            if end > current_start:
                silences.append(SilenceInterval(start=round(current_start, 3), end=round(end, 3)))
            current_start = None

    if current_start is not None and total_duration and total_duration > current_start:
        silences.append(SilenceInterval(start=round(current_start, 3), end=round(total_duration, 3)))
    return silences


def detect_audio_peaks(
    path: Path,
    window_sec: float = 1.0,
    top_k: int = 8,
    min_gap_sec: float = 3.0,
) -> list[AudioPeak]:
    """Measure per-window RMS loudness to flag splashes, drag screams, and hookset shouts."""
    ffmpeg = resolve_ffmpeg().path
    # Extract mono 8kHz s16le PCM to compute exact RMS per window in pure Python (zero numpy dependency).
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    raw = proc.stdout
    if not raw or len(raw) < 16:
        return []

    import array

    samples = array.array("h")
    usable_len = (len(raw) // 2) * 2
    samples.frombytes(raw[:usable_len])
    step = max(1, int(8000 * window_sec))
    window_dbs: list[tuple[float, float]] = []
    for idx in range(0, len(samples), step):
        chunk = samples[idx : idx + step]
        if not chunk:
            continue
        mean_sq = sum(s * s for s in chunk) / len(chunk)
        rms = math.sqrt(mean_sq) / 32768.0
        db = 20.0 * math.log10(max(rms, 1e-6))
        window_dbs.append((round((idx + len(chunk) / 2) / 8000.0, 2), round(db, 1)))

    if not window_dbs:
        return []

    sorted_dbs = sorted(db for _, db in window_dbs)
    median_db = sorted_dbs[len(sorted_dbs) // 2]
    threshold = max(median_db + 4.5, -26.0)

    candidates = [(t, db) for t, db in window_dbs if db >= threshold]
    candidates.sort(key=lambda item: item[1], reverse=True)

    picked: list[AudioPeak] = []
    for t, db in candidates:
        if all(abs(t - p.time) >= min_gap_sec for p in picked):
            picked.append(AudioPeak(time=t, db=db))
        if len(picked) >= top_k:
            break
    picked.sort(key=lambda p: p.time)
    return picked


def detect_scene_cuts(path: Path, threshold: float = 0.35, max_cuts: int = 120) -> list[float]:
    """Detect visual shot boundaries using FFmpeg scene change filter."""
    ffmpeg = resolve_ffmpeg().path
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    cuts: list[float] = []
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"pts_time:\s*(\d+(?:\.\d+)?)", line)
        if m:
            t = round(float(m.group(1)), 3)
            if not cuts or t - cuts[-1] >= 1.0:
                cuts.append(t)
                if len(cuts) >= max_cuts:
                    break
    return cuts


def generate_proxy(source: Path, dest: Path, max_side: int = 720) -> Path:
    """Create a lightweight H.264 proxy for fast preview & Antigravity native video review."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg().path
    scale_expr = (
        f"scale='if(gte(iw,ih),min({max_side},iw),-2)':'if(gte(iw,ih),-2,min({max_side},ih))'"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        scale_expr,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to generate proxy for {source.name}: {proc.stderr.strip()}")
    return dest


def format_tc(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def find_cjk_font(bold: bool = True) -> str | None:
    """Locate a reliable Chinese-capable font across macOS, Windows, and Linux."""
    candidates = [
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        # Windows
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def generate_contact_sheet(
    source: Path,
    dest: Path,
    duration: float,
    cols: int = 4,
    max_frames: int = 16,
    thumb_width: int = 320,
    title: str = "",
) -> Path:
    """Extract evenly spaced frames with burned-in timecodes into a contact sheet JPEG (for Codex & human review)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg().path
    count = max(1, min(max_frames, max(4, int(math.ceil(duration / 4.0)))))
    if duration <= 0.2:
        timestamps = [0.0]
    elif count == 1:
        timestamps = [round(duration / 2.0, 2)]
    else:
        step = duration / count
        timestamps = [round(min(duration - 0.05, step * (i + 0.5)), 2) for i in range(count)]

    tmp_dir = dest.parent / f".tmp_{dest.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[float, Image.Image]] = []
    try:
        for i, ts in enumerate(timestamps):
            frame_path = tmp_dir / f"f_{i:03d}.jpg"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{ts:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={thumb_width}:-2",
                    str(frame_path),
                ],
                capture_output=True,
                check=False,
            )
            if frame_path.exists():
                with Image.open(frame_path) as im:
                    frames.append((ts, im.convert("RGB")))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not frames:
        raise RuntimeError(f"Could not extract any frames for contact sheet from {source.name}")

    tw, th = frames[0][1].size
    rows = math.ceil(len(frames) / cols)
    header_h = 44 if title else 12
    pad = 8
    sheet_w = cols * tw + (cols + 1) * pad
    sheet_h = header_h + rows * th + (rows + 1) * pad
    canvas = Image.new("RGB", (sheet_w, sheet_h), (18, 22, 28))
    draw = ImageDraw.Draw(canvas)

    font_path = find_cjk_font(bold=True)
    header_font = ImageFont.truetype(font_path, 20) if font_path else ImageFont.load_default()
    badge_font = ImageFont.truetype(font_path, 16) if font_path else ImageFont.load_default()

    if title:
        draw.text((pad, 10), title, fill=(235, 240, 245), font=header_font)

    for idx, (ts, im) in enumerate(frames):
        r, c = divmod(idx, cols)
        x = pad + c * (tw + pad)
        y = header_h + pad + r * (th + pad)
        if im.size != (tw, th):
            im = im.resize((tw, th))
        canvas.paste(im, (x, y))
        label = format_tc(ts)
        draw.rectangle([x + 6, y + 6, x + 78, y + 30], fill=(0, 0, 0))
        draw.text((x + 12, y + 8), label, fill=(255, 214, 82), font=badge_font)

    canvas.save(dest, format="JPEG", quality=85)
    return dest


def transcode_for_davinci_free(source: Path, dest: Path, codec: str = "h264_8bit") -> Path:
    """Convert 10-bit footage to DaVinci-Resolve-Free-compatible 8-bit H.264 or ProRes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg().path
    if codec == "prores":
        vargs = ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"]
    else:
        vargs = ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p"]
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        *vargs,
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Transcode failed for {source.name}: {proc.stderr.strip()}")
    return dest
