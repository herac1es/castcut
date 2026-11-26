"""Stage 4: Automated Quality Control gate for rendered preview & NLE export integrity."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from castcut.media import generate_contact_sheet, probe_video, resolve_ffmpeg
from castcut.models import QcCheck, QcReport
from castcut.workspace import EpisodePaths, load_edit_plan, load_media_index


def _detect_black_frames(video_path: Path, min_black_sec: float = 0.8) -> list[tuple[float, float]]:
    ffmpeg = resolve_ffmpeg().path
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            f"blackdetect=d={min_black_sec}:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    blacks: list[tuple[float, float]] = []
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)", line)
        if m:
            blacks.append((float(m.group(1)), float(m.group(2))))
    return blacks


def run_qc(episode_dir: Path, stage: str = "fine") -> QcReport:
    """Validate preview MP4, source links, 10-bit DaVinci alerts, and generate QC contact sheet."""
    paths = EpisodePaths(episode_dir)
    media_index = load_media_index(paths.media_index_json)
    plan_path = paths.fine_cut_yaml if stage == "fine" and paths.fine_cut_yaml.exists() else paths.rough_cut_yaml
    plan = load_edit_plan(plan_path)
    preview = paths.fine_preview_mp4 if stage == "fine" and paths.fine_preview_mp4.exists() else paths.rough_preview_mp4

    checks: list[QcCheck] = []

    # 1. Source files exist on disk (prevents red Media Offline in DaVinci Resolve)
    missing_sources = [
        item.source_rel_path
        for item in plan.items
        if not Path(item.source_abs_path).exists() and not (paths.root / item.source_rel_path).exists()
    ]
    if missing_sources:
        checks.append(
            QcCheck(
                name="source_links",
                status="fail",
                detail=f"缺失 {len(missing_sources)} 个原素材文件: {', '.join(missing_sources[:3])}",
            )
        )
    else:
        checks.append(
            QcCheck(
                name="source_links",
                status="pass",
                detail=f"全部 {len(plan.items)} 段引用原素材路径有效。",
            )
        )

    # 2. 10-bit DaVinci Resolve Free advisory
    if media_index.has_10bit_media:
        ten_bit = [m.filename for m in media_index.files if m.is_10bit]
        checks.append(
            QcCheck(
                name="davinci_free_10bit",
                status="warn",
                detail=(
                    f"检测到 {len(ten_bit)} 个 10-bit 素材 ({', '.join(ten_bit[:3])})。"
                    "Windows 达芬奇免费版可能离线，可先执行 `castcut prepare-davinci` 转 8-bit。"
                ),
            )
        )
    else:
        checks.append(
            QcCheck(
                name="davinci_free_10bit",
                status="pass",
                detail="全部素材均为 8-bit，达芬奇免费版全平台直接兼容。",
            )
        )

    # 3. Preview render checks
    qc_contact_rel: str | None = None
    if not preview.exists():
        checks.append(
            QcCheck(
                name="preview_exists",
                status="fail",
                detail=f"未找到预览成片：{preview.name}，请先运行 `castcut render`。",
            )
        )
    else:
        probed = probe_video(preview, media_id="QC", rel_path=preview.relative_to(paths.root).as_posix())
        dur_diff = abs(probed.duration - plan.total_duration)
        if dur_diff > 0.6:
            checks.append(
                QcCheck(
                    name="duration_sync",
                    status="warn",
                    detail=f"成片时长 {probed.duration:.2f}s 与计划 {plan.total_duration:.2f}s 偏差 {dur_diff:.2f}s。",
                )
            )
        else:
            checks.append(
                QcCheck(
                    name="duration_sync",
                    status="pass",
                    detail=f"成片时长 {probed.duration:.2f}s 与计划 {plan.total_duration:.2f}s 一致（偏差 {dur_diff:.2f}s）。",
                )
            )

        if not probed.has_audio:
            checks.append(
                QcCheck(name="audio_stream", status="fail", detail="预览成片缺少音频轨！")
            )
        else:
            checks.append(
                QcCheck(
                    name="audio_stream",
                    status="pass",
                    detail=f"音视频轨完整 ({probed.width}x{probed.height}, {probed.video_codec}/{probed.audio_codec})。",
                )
            )

        blacks = _detect_black_frames(preview)
        if blacks:
            checks.append(
                QcCheck(
                    name="black_frames",
                    status="warn",
                    detail=f"检测到 {len(blacks)} 处较长黑屏段，首处 {blacks[0][0]:.2f}s–{blacks[0][1]:.2f}s。",
                )
            )
        else:
            checks.append(QcCheck(name="black_frames", status="pass", detail="未检测到异常黑屏。"))

        generate_contact_sheet(
            preview,
            paths.qc_contact_jpg,
            duration=probed.duration,
            cols=4,
            max_frames=16,
            title=f"QC | {plan.title} | {probed.width}x{probed.height} | {probed.duration:.1f}s",
        )
        qc_contact_rel = paths.qc_contact_jpg.relative_to(paths.root).as_posix()

    # 4. NLE export artifacts presence
    exported = [
        name
        for name, p in (
            ("OTIO", paths.timeline_otio),
            ("FCPXML", paths.timeline_fcpxml),
            ("SRT", paths.subtitles_srt),
        )
        if p.exists()
    ]
    if exported:
        checks.append(
            QcCheck(name="nle_exports", status="pass", detail=f"已生成交接产物：{', '.join(exported)}。")
        )
    else:
        checks.append(
            QcCheck(name="nle_exports", status="warn", detail="尚未运行 `castcut export` 生成 NLE 工程文件。")
        )

    passed = all(c.status != "fail" for c in checks)
    report = QcReport(
        episode_id=plan.episode_id,
        passed=passed,
        checks=checks,
        preview_path=preview.relative_to(paths.root).as_posix() if preview.exists() else None,
        qc_contact_sheet=qc_contact_rel,
    )
    paths.qc_report_json.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
