"""Deterministic FFmpeg preview & delivery renderer with mixed-orientation adaptation and BGM ducking."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from castcut.exporters.srt import write_srt
from castcut.media import resolve_ffmpeg
from castcut.models import EditPlan, MixedOrientationPolicy, TimelineClipItem
from castcut.overlays import materialize_overlays
from castcut.workspace import (
    EpisodePaths,
    load_edit_plan,
    load_media_index,
    load_project_config,
    save_edit_plan,
)


def build_atempo_chain(speed: float) -> str:
    """Decompose any playback speed (>0) into chained FFmpeg atempo filters within [0.5, 2.0]."""
    if abs(speed - 1.0) < 1e-3:
        return "anull"
    factors: list[float] = []
    remaining = max(0.125, min(16.0, speed))
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={f:.5f}" for f in factors)


def _db_to_linear(db: float) -> float:
    return round(math.pow(10.0, db / 20.0), 6)


def _bgm_filter_expr(plan: EditPlan, in_label: str, out_label: str) -> str:
    assert plan.bgm is not None
    base_lin = _db_to_linear(plan.bgm.base_volume_db)
    duck_lin = _db_to_linear(plan.bgm.duck_volume_db)
    if plan.bgm.ducking_intervals:
        cond = "+".join(
            f"between(t,{iv.start:.3f},{iv.end:.3f})" for iv in plan.bgm.ducking_intervals
        )
        vol_expr = f"if(gt({cond},0),{duck_lin},{base_lin})"
    else:
        vol_expr = f"{base_lin}"

    fade_out_start = max(0.0, plan.total_duration - plan.bgm.fade_out_sec)
    return (
        f"[{in_label}]atrim=0:{plan.total_duration:.3f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,"
        f"volume='{vol_expr}':eval=frame,"
        f"afade=t=in:st=0:d={plan.bgm.fade_in_sec:.2f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={plan.bgm.fade_out_sec:.2f}[{out_label}]"
    )


def render_ducked_bgm_wav(plan: EditPlan, dest: Path) -> Path | None:
    """Pre-render looped, faded, and auto-ducked BGM WAV so DaVinci Resolve A2 sounds mixed immediately."""
    if not plan.bgm or not Path(plan.bgm.path).exists() or plan.total_duration <= 0:
        return None
    ffmpeg = resolve_ffmpeg().path
    dest.parent.mkdir(parents=True, exist_ok=True)
    filt = _bgm_filter_expr(plan, "0:a", "bgm_a")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        plan.bgm.path,
        "-filter_complex",
        filt,
        "-map",
        "[bgm_a]",
        "-t",
        f"{plan.total_duration:.3f}",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed rendering ducked BGM WAV: {proc.stderr.strip()}")
    return dest


def render_adapted_mixed_clip(
    item: TimelineClipItem,
    source_path: Path,
    has_audio: bool,
    width: int,
    height: int,
    fps: float,
    dest: Path,
) -> Path:
    """Pre-bake an orientation-mismatched clip (e.g. vertical in 16:9) with blur_pillarbox for NLE import."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    return _render_segment(
        item=item,
        source_path=source_path,
        has_audio=has_audio,
        width=width,
        height=height,
        fps=fps,
        crf=17,
        preset="fast",
        dest=dest,
    )


def _video_framing_filter(
    item: TimelineClipItem,
    width: int,
    height: int,
    fps: float,
    in_label: str = "0:v",
    out_label: str = "vout",
) -> str:
    """Build video filtergraph normalizing speed, mixed orientation, fps, and square pixels."""
    pts_part = f"setpts=(PTS-STARTPTS)/{item.speed:.5f}" if abs(item.speed - 1.0) >= 1e-3 else "setpts=PTS-STARTPTS"
    fps_part = f"fps={fps:.3f},setsar=1,format=yuv420p"

    if item.framing == MixedOrientationPolicy.BLUR_PILLARBOX:
        return (
            f"[{in_label}]{pts_part},split=2[bg_raw][fg_raw];"
            f"[bg_raw]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=24,eq=brightness=-0.12[bg];"
            f"[fg_raw]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{fps_part}[{out_label}]"
        )
    if item.framing == MixedOrientationPolicy.CROP_FILL:
        return (
            f"[{in_label}]{pts_part},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},{fps_part}[{out_label}]"
        )
    # FIT (default when clip orientation already matches canvas)
    return (
        f"[{in_label}]{pts_part},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,{fps_part}[{out_label}]"
    )


def _render_segment(
    item: TimelineClipItem,
    source_path: Path,
    has_audio: bool,
    width: int,
    height: int,
    fps: float,
    crf: int,
    preset: str,
    dest: Path,
) -> Path:
    ffmpeg = resolve_ffmpeg().path
    src_dur = item.source_duration
    out_dur = item.output_duration
    v_filter = _video_framing_filter(item, width, height, fps)

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{item.source_start:.3f}",
        "-t",
        f"{src_dur:.3f}",
        "-i",
        str(source_path),
    ]

    if has_audio and not item.mute_audio:
        atempo = build_atempo_chain(item.speed)
        gain_expr = f",volume={item.audio_gain_db:.1f}dB" if abs(item.audio_gain_db) >= 0.1 else ""
        a_filter = (
            f"[0:a]asetpts=PTS-STARTPTS,{atempo}{gain_expr},"
            f"aresample=48000,aformat=channel_layouts=stereo[aout]"
        )
        fc = f"{v_filter};{a_filter}"
        cmd.extend(["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"])
    else:
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{out_dur:.3f}",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-filter_complex",
                v_filter,
                "-map",
                "[vout]",
                "-map",
                "1:a",
            ]
        )

    cmd.extend(
        [
            "-t",
            f"{out_dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(dest),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed rendering segment {item.item_id}: {proc.stderr.strip()}")
    return dest


def _escape_ffmpeg_sub_path(path: Path) -> str:
    # Escape colons and backslashes for FFmpeg subtitles filter on Windows/macOS/Linux.
    s = path.resolve().as_posix()
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _compose_final(
    base_video: Path,
    plan: EditPlan,
    episode_root: Path,
    srt_path: Path | None,
    burn_subtitles: bool,
    crf: int,
    preset: str,
    dest: Path,
) -> Path:
    """Layer V2 PNG HUD overlays, looped/ducked BGM, and optional preview subtitles onto base concat."""
    ffmpeg = resolve_ffmpeg().path
    active_overlays = [
        ov
        for ov in plan.overlays
        if ov.png_rel_path and (episode_root / ov.png_rel_path).exists()
    ]
    active_voiceovers = [
        vo
        for vo in plan.voiceovers
        if vo.audio_rel_path and (episode_root / vo.audio_rel_path).exists()
    ]
    has_bgm = bool(plan.bgm and Path(plan.bgm.path).exists())
    should_burn_subs = bool(burn_subtitles and srt_path and srt_path.exists() and plan.subtitles)

    if not active_overlays and not active_voiceovers and not has_bgm and not should_burn_subs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_video, dest)
        return dest

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(base_video)]
    input_idx = 1

    overlay_inputs: list[tuple[int, float, float]] = []
    for ov in active_overlays:
        cmd.extend(["-i", str(episode_root / ov.png_rel_path)])
        overlay_inputs.append((input_idx, ov.start, ov.start + ov.duration))
        input_idx += 1

    vo_inputs: list[tuple[int, float, float, float, float]] = []
    for vo in active_voiceovers:
        assert vo.audio_rel_path is not None
        cmd.extend(["-i", str(episode_root / vo.audio_rel_path)])
        vo_inputs.append((input_idx, vo.start, max(0.2, vo.duration), vo.gain_db, vo.duck_field_audio_db))
        input_idx += 1

    bgm_input_idx: int | None = None
    if has_bgm and plan.bgm is not None:
        cmd.extend(["-stream_loop", "-1", "-i", plan.bgm.path])
        bgm_input_idx = input_idx
        input_idx += 1

    filter_parts: list[str] = []
    v_cur = "0:v"
    for k, (ov_i, s, e) in enumerate(overlay_inputs):
        nxt = f"v_ov_{k}"
        filter_parts.append(
            f"[{v_cur}][{ov_i}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[{nxt}]"
        )
        v_cur = nxt

    if should_burn_subs and srt_path is not None:
        escaped = _escape_ffmpeg_sub_path(srt_path)
        nxt = "v_sub"
        # Respect coolbot subtitle safe margins (left: 48, right: 148, bottom: 48 in 1080p coordinates)
        filter_parts.append(
            f"[{v_cur}]subtitles='{escaped}':force_style='FontSize=20,Outline=2,Shadow=1,MarginL=28,MarginR=74,MarginV=28'[{nxt}]"
        )
        v_cur = nxt

    a_map = "0:a"
    audio_mix_labels: list[str] = []
    if vo_inputs or bgm_input_idx is not None:
        # Duck field audio during Doubao TTS voiceovers while preserving soft outdoor ambience
        if vo_inputs:
            duck_lin = _db_to_linear(min(d for _, _, _, _, d in vo_inputs))
            cond = "+".join(
                f"between(t,{max(0.0, st - 0.1):.3f},{st + dur + 0.15:.3f})"
                for _, st, dur, _, _ in vo_inputs
            )
            filter_parts.append(
                f"[0:a]volume='if(gt({cond},0),{duck_lin},1.0)':eval=frame[field_a]"
            )
            audio_mix_labels.append("[field_a]")
        else:
            audio_mix_labels.append("[0:a]")

        for k, (vo_i, st, _, gain_db, _) in enumerate(vo_inputs):
            delay_ms = max(0, int(round(st * 1000)))
            gain_part = f",volume={gain_db:.1f}dB" if abs(gain_db) >= 0.1 else ""
            delay_part = f",adelay={delay_ms}|{delay_ms}" if delay_ms > 0 else ""
            filter_parts.append(
                f"[{vo_i}:a]aresample=48000,aformat=channel_layouts=stereo{gain_part}{delay_part}[vo_{k}]"
            )
            audio_mix_labels.append(f"[vo_{k}]")

        if bgm_input_idx is not None and plan.bgm is not None:
            filter_parts.append(_bgm_filter_expr(plan, f"{bgm_input_idx}:a", "bgm_a"))
            audio_mix_labels.append("[bgm_a]")

        filter_parts.append(
            f"{''.join(audio_mix_labels)}amix=inputs={len(audio_mix_labels)}:duration=first:dropout_transition=0:normalize=0[a_mix]"
        )
        a_map = "[a_mix]"

    cmd.extend(["-filter_complex", ";".join(filter_parts)])
    v_map = f"[{v_cur}]" if v_cur != "0:v" else "0:v"
    cmd.extend(
        [
            "-map",
            v_map,
            "-map",
            a_map,
            "-t",
            f"{plan.total_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Final compose failed: {proc.stderr.strip()}")
    return dest


def render_episode(
    episode_dir: Path,
    stage: str = "fine",
    use_proxies: bool | None = None,
    burn_subtitles: bool | None = None,
) -> Path:
    """Render rough_preview.mp4 (fast proxy) or fine_preview.mp4 (full quality + overlays + BGM)."""
    paths = EpisodePaths(episode_dir)
    cfg = load_project_config(paths.project_yaml)
    media_index = load_media_index(paths.media_index_json)
    media_map = {m.media_id: m for m in media_index.files}

    plan_path = paths.rough_cut_yaml if stage == "rough" else paths.fine_cut_yaml
    plan = load_edit_plan(plan_path)
    plan.recompute_timings()
    if not plan.items:
        raise ValueError(f"Edit plan {plan_path} has no timeline items to render.")

    is_rough = stage == "rough"
    proxy_mode = is_rough if use_proxies is None else use_proxies
    render_w = min(plan.width, 1280) if is_rough and plan.width >= plan.height else (
        min(plan.width, 720) if is_rough else plan.width
    )
    render_h = int(round(render_w * plan.height / plan.width / 2.0)) * 2
    crf = 26 if is_rough else 19
    preset = "ultrafast" if is_rough else "fast"
    dest = paths.rough_preview_mp4 if is_rough else paths.fine_preview_mp4

    # Materialize V2 overlays & SRT for fine cut (and persist updated png_rel_path back to plan)
    srt_path: Path | None = None
    if not is_rough:
        materialize_overlays(plan, paths.root, paths.overlays_dir)
        save_edit_plan(plan_path, plan)
        if plan.subtitles:
            srt_path = write_srt(plan.subtitles, paths.subtitles_srt)

    ffmpeg = resolve_ffmpeg().path
    with tempfile.TemporaryDirectory(prefix="castcut_render_") as tmp:
        tmp_dir = Path(tmp)
        seg_files: list[Path] = []
        for idx, item in enumerate(plan.items):
            media = media_map[item.media_id]
            src = Path(item.source_abs_path)
            if proxy_mode and media.proxy_rel_path:
                cand_proxy = paths.root / media.proxy_rel_path
                if cand_proxy.exists():
                    src = cand_proxy
            if not src.exists():
                src = paths.root / item.source_rel_path
            if not src.exists():
                raise FileNotFoundError(f"Source clip missing for {item.item_id}: {src}")

            seg_out = tmp_dir / f"seg_{idx:04d}.mp4"
            _render_segment(
                item=item,
                source_path=src,
                has_audio=media.has_audio,
                width=render_w,
                height=render_h,
                fps=plan.fps,
                crf=crf,
                preset=preset,
                dest=seg_out,
            )
            seg_files.append(seg_out)

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in seg_files) + "\n",
            encoding="utf-8",
        )
        base_concat = tmp_dir / "base_concat.mp4"
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(base_concat),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Segment concat failed: {proc.stderr.strip()}")

        if is_rough:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base_concat, dest)
            return dest

        burn_flag = cfg.burn_subtitles_in_preview if burn_subtitles is None else burn_subtitles
        return _compose_final(
            base_video=base_concat,
            plan=plan,
            episode_root=paths.root,
            srt_path=srt_path,
            burn_subtitles=burn_flag,
            crf=crf,
            preset=preset,
            dest=dest,
        )
