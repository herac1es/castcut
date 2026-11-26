"""CastCut CLI: Agent-first lure fishing vlog editing pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from castcut import __version__
from castcut.asr import faster_whisper_available
from castcut.exporters import export_episode
from castcut.ingest import ingest_episode
from castcut.media import (
    find_cjk_font,
    resolve_ffmpeg,
    resolve_ffprobe,
    transcode_for_davinci_free,
)
from castcut.models import (
    ClipCategory,
    MixedOrientationPolicy,
    ResolutionTier,
    TemplateType,
    parse_aspect_mode,
)
from castcut.planner import build_fine_plan, build_rough_plan
from castcut.qc import run_qc
from castcut.renderer import render_episode
from castcut.workspace import (
    EpisodePaths,
    init_episode_workspace,
    load_clip_catalog,
    load_edit_plan,
    load_media_index,
    save_clip_catalog,
    save_edit_plan,
)

app = typer.Typer(
    name="castcut",
    help="路亚钓鱼 Vlog Agent 剪辑流水线（挑片 -> 粗剪 -> 精剪 -> 零 VIP 工程导出）。",
    no_args_is_help=True,
)
console = Console()


@app.command()
def doctor() -> None:
    """检查本机 FFmpeg、字体、ASR 与 MCP 环境。"""
    table = Table(title=f"CastCut v{__version__} 环境体检")
    table.add_column("组件", style="cyan")
    table.add_column("状态", style="green")
    table.add_column("详情")

    try:
        ff = resolve_ffmpeg()
        status = "OK" if ff.major >= 5 else "WARN"
        table.add_row("FFmpeg", status, f"{ff.version} ({ff.source}) -> {ff.path}")
    except RuntimeError as exc:
        table.add_row("FFmpeg", "FAIL", str(exc))

    fp = resolve_ffprobe()
    if fp:
        table.add_row("FFprobe", "OK", f"{fp.version} ({fp.source}) -> {fp.path}")
    else:
        table.add_row("FFprobe", "WARN", "未检测到系统 ffprobe，将自动降级使用 ffmpeg 探测")

    font = find_cjk_font()
    table.add_row("中文字体", "OK" if font else "WARN", font or "未找到中文字体，HUD 卡片中文可能乱码")

    has_asr = faster_whisper_available()
    table.add_row(
        "本地 ASR (faster-whisper)",
        "OK" if has_asr else "OPTIONAL",
        "已安装，可直接转写" if has_asr else "未安装（可 `uv sync --extra asr` 或放入 .srt 旁白文件）",
    )

    try:
        import mcp  # noqa: F401

        table.add_row("MCP Server", "OK", "已安装，可运行 `castcut-mcp` 供 Codex / Antigravity 调用")
    except ImportError:
        table.add_row("MCP Server", "OPTIONAL", "未安装（可 `uv sync --extra mcp` 启用）")

    console.print(table)


@app.command()
def init(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录路径，例如 episodes/2026-spring-bass")],
    title: Annotated[str, typer.Option("--title", "-t", help="单集标题")],
    aspect: Annotated[str, typer.Option(help="画幅模式：auto / 16:9(landscape) / 3:4(portrait，对齐 coolbot)")] = "auto",
    resolution: Annotated[ResolutionTier, typer.Option(help="分辨率：1080p 或 4k")] = ResolutionTier.FHD_1080P,
    mixed: Annotated[MixedOrientationPolicy, typer.Option(help="异画幅混剪策略：blur_pillarbox / crop_fill / fit")] = MixedOrientationPolicy.BLUR_PILLARBOX,
    template: Annotated[TemplateType, typer.Option(help="叙事模板：fishing-vlog / short-highlight")] = TemplateType.FISHING_VLOG,
    target_duration: Annotated[float | None, typer.Option(help="目标成片时长（秒）")] = None,
    location: Annotated[str, typer.Option(help="钓点/场景简介")] = "",
    bgm: Annotated[str | None, typer.Option(help="可选 BGM 音频路径")] = None,
    handle: Annotated[str, typer.Option(help="频道角标（默认对齐 coolbot：@路亚知马力，置空关闭）")] = "@路亚知马力",
) -> None:
    """初始化一集新的路亚 Vlog 工作区。"""
    cfg = init_episode_workspace(
        episode_dir=episode_dir,
        title=title,
        aspect=parse_aspect_mode(aspect),
        resolution=resolution,
        mixed_orientation=mixed,
        template=template,
        target_duration_sec=target_duration,
        location=location,
        bgm_path=bgm,
        channel_handle=handle,
    )
    console.print(f"[green]已初始化单集工作区[/green]: {episode_dir.resolve()}")
    console.print(f"  配置: {episode_dir / 'project.yaml'}")
    console.print(f"  请把原始素材放入: {episode_dir / 'raw'}")
    console.print(f"  画幅策略: aspect={cfg.aspect.value}, mixed={cfg.mixed_orientation.value}, template={cfg.template.value}")


@app.command()
def ingest(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    asr: Annotated[str, typer.Option(help="转写模式：auto / faster-whisper / none")] = "auto",
    whisper_model: Annotated[str, typer.Option(help="Whisper 模型大小（tiny/base/small/medium）")] = "small",
    proxy: Annotated[bool, typer.Option("--proxy/--no-proxy", help="是否生成 720p 代理片（供 Antigravity 看片与快速预览）")] = True,
    contacts: Annotated[bool, typer.Option("--contacts/--no-contacts", help="是否生成带时间码拼图（供 Codex 视觉挑片）")] = True,
) -> None:
    """Stage 1: 扫描 raw/、探测元数据、音频峰值/场景切点/转写、生成代理片与候选镜头库。"""
    media_index, catalog = ingest_episode(
        episode_dir=episode_dir,
        asr_mode=asr,
        whisper_model=whisper_model,
        make_proxies=proxy,
        make_contacts=contacts,
    )
    console.print(
        f"[green]Ingest 完成[/green]: {len(media_index.files)} 个原素材，"
        f"总时长 {media_index.total_duration:.1f}s，"
        f"主导画幅 [bold]{media_index.dominant_orientation}[/bold] "
        f"(横屏 {media_index.landscape_duration:.1f}s / 竖屏 {media_index.portrait_duration:.1f}s)，"
        f"切分候选镜头 {len(catalog.clips)} 段。"
    )
    if media_index.has_10bit_media:
        console.print(
            "[yellow]提示[/yellow]: 检测到 10-bit 素材，若在 Windows 达芬奇免费版编辑，可运行 `castcut prepare-davinci`。"
        )


@app.command()
def clips(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    only_kept: Annotated[bool, typer.Option("--only-kept", help="仅显示已保留镜头")] = False,
) -> None:
    """查看 Stage 1 切分后的候选镜头与挑片打标状态。"""
    paths = EpisodePaths(episode_dir)
    catalog = load_clip_catalog(paths.clips_json)
    table = Table(title=f"候选镜头清单 ({catalog.episode_id})")
    table.add_column("ID", style="cyan")
    table.add_column("素材")
    table.add_column("区间(s)")
    table.add_column("画幅")
    table.add_column("分类", style="magenta")
    table.add_column("评分")
    table.add_column("保留")
    table.add_column("关键词 / 字幕摘要")

    for c in catalog.clips:
        if only_kept and not c.keep:
            continue
        summary = " ".join(c.keywords_hit[:3])
        if c.transcript_text:
            summary = f"[{summary}] {c.transcript_text[:36]}" if summary else c.transcript_text[:42]
        table.add_row(
            c.clip_id,
            c.media_id,
            f"{c.start:.1f}-{c.end:.1f} ({c.duration:.1f}s)",
            c.orientation[:4],
            c.category.value,
            f"{c.score:.2f}",
            "Y" if c.keep else "N",
            summary or c.agent_note[:40],
        )
    console.print(table)


@app.command()
def label(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    clip_id: Annotated[str, typer.Argument(help="镜头 ID，如 C001")],
    category: Annotated[ClipCategory | None, typer.Option(help="修改镜头分类")] = None,
    score: Annotated[float | None, typer.Option(help="修改评分 0.0-1.0")] = None,
    keep: Annotated[bool | None, typer.Option("--keep/--drop", help="是否保留该镜头")] = None,
    start: Annotated[float | None, typer.Option(help="微调起始秒")] = None,
    end: Annotated[float | None, typer.Option(help="微调结束秒")] = None,
    note: Annotated[str | None, typer.Option(help="Agent 或人工备注")] = None,
) -> None:
    """更新单个候选镜头的分类、评分、起止点或留弃状态（挑片阶段）。"""
    paths = EpisodePaths(episode_dir)
    catalog = load_clip_catalog(paths.clips_json)
    target = next((c for c in catalog.clips if c.clip_id == clip_id), None)
    if target is None:
        raise typer.BadParameter(f"未找到镜头 {clip_id}")

    if category is not None:
        target.category = category
        if category == ClipCategory.WASTE and keep is None:
            target.keep = False
    if score is not None:
        target.score = round(max(0.0, min(1.0, score)), 2)
    if keep is not None:
        target.keep = keep
    if start is not None:
        target.start = round(max(0.0, start), 3)
    if end is not None:
        target.end = round(max(target.start + 0.2, end), 3)
    target.duration = round(max(0.0, target.end - target.start), 3)
    if note is not None:
        target.agent_note = note

    save_clip_catalog(paths.clips_json, catalog)
    console.print(
        f"[green]已更新 {clip_id}[/green]: category={target.category.value}, "
        f"score={target.score:.2f}, keep={target.keep}, range={target.start:.2f}-{target.end:.2f}s"
    )


@app.command()
def plan(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    stage: Annotated[str, typer.Option(help="计划阶段：rough（粗剪）或 fine（精剪）")] = "rough",
    template: Annotated[TemplateType | None, typer.Option(help="覆盖叙事模板")] = None,
) -> None:
    """Stage 2/3: 生成可编辑的 rough_cut.yaml（粗剪）或 fine_cut.yaml（精剪）。"""
    if stage == "rough":
        ep = build_rough_plan(episode_dir, template_override=template)
        out_file = EpisodePaths(episode_dir).rough_cut_yaml
    elif stage == "fine":
        ep = build_fine_plan(episode_dir)
        out_file = EpisodePaths(episode_dir).fine_cut_yaml
    else:
        raise typer.BadParameter("stage 只能是 rough 或 fine")

    console.print(
        f"[green]{stage} 剪辑单已生成[/green]: {out_file} "
        f"({len(ep.items)} 段镜头，画幅 {ep.width}x{ep.height}，预计总时长 {ep.total_duration:.1f}s，"
        f"字幕 {len(ep.subtitles)} 条，HUD 卡片 {len(ep.overlays)} 张)"
    )


@app.command()
def render(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    stage: Annotated[str, typer.Option(help="渲染阶段：rough 或 fine")] = "fine",
    proxy: Annotated[bool | None, typer.Option("--proxy/--full", help="是否优先使用代理片渲染")] = None,
    burn_subs: Annotated[bool | None, typer.Option("--burn-subs/--no-burn-subs", help="预览片是否烧录硬字幕")] = None,
) -> None:
    """渲染粗剪代理预览片或精剪审片成片 MP4。"""
    out = render_episode(
        episode_dir=episode_dir,
        stage=stage,
        use_proxies=proxy,
        burn_subtitles=burn_subs,
    )
    console.print(f"[green]渲染完成[/green]: {out}")


@app.command("export")
def export_cmd(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    formats: Annotated[str, typer.Option(help="逗号分隔导出格式：otio,fcpxml,srt,jianying")] = "otio,fcpxml,srt",
    stage: Annotated[str, typer.Option(help="导出基准计划：fine 或 rough")] = "fine",
    bake_mixed: Annotated[bool, typer.Option("--bake-mixed/--raw-mixed", help="将横竖混拍片段预烘焙为高斯模糊背景片，达芬奇打开即无黑边")] = True,
    bake_bgm: Annotated[bool, typer.Option("--bake-bgm/--raw-bgm", help="预生成已自动闪避的 bgm_ducked.wav 挂到 A2 轨")] = True,
) -> None:
    """导出零 VIP 工程文件（默认 OTIO + FCPXML + SRT，直接导入达芬奇免费版）。"""
    fmt_list = [f.strip() for f in formats.split(",") if f.strip()]
    results = export_episode(
        episode_dir=episode_dir,
        formats=fmt_list,
        stage=stage,
        bake_mixed=bake_mixed,
        bake_ducked_bgm=bake_bgm,
    )
    for key, path in results.items():
        console.print(f"[green]已导出 {key.upper()}[/green]: {path}")


@app.command()
def qc(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    stage: Annotated[str, typer.Option(help="质检阶段：fine 或 rough")] = "fine",
) -> None:
    """Stage 4: 运行成片与工程交接自动质检，并生成成片验收拼图。"""
    report = run_qc(episode_dir=episode_dir, stage=stage)
    table = Table(title=f"QC 报告 ({report.episode_id}) — {'PASS' if report.passed else 'FAIL'}")
    table.add_column("检查项", style="cyan")
    table.add_column("状态")
    table.add_column("说明")
    for chk in report.checks:
        color = {"pass": "green", "warn": "yellow", "fail": "red"}[chk.status]
        table.add_row(chk.name, f"[{color}]{chk.status.upper()}[/{color}]", chk.detail)
    console.print(table)
    if report.qc_contact_sheet:
        console.print(f"验收拼图: {EpisodePaths(episode_dir).root / report.qc_contact_sheet}")
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("prepare-davinci")
def prepare_davinci(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    codec: Annotated[str, typer.Option(help="转码格式：h264_8bit（推荐，体积小）或 prores")] = "h264_8bit",
) -> None:
    """将 10-bit 原素材批量转为 Windows 达芬奇免费版可直接读取的 8-bit/ProRes 中间片并更新引用。"""
    paths = EpisodePaths(episode_dir)
    media_index = load_media_index(paths.media_index_json)
    ten_bit = [m for m in media_index.files if m.is_10bit]
    if not ten_bit:
        console.print("[green]未发现 10-bit 素材，无需转码，可直接导入达芬奇免费版。[/green]")
        return

    out_dir = paths.work_dir / "davinci_ready"
    ext = ".mov" if codec == "prores" else ".mp4"
    remapped: dict[str, tuple[str, str]] = {}

    for m in ten_bit:
        src = Path(m.abs_path)
        dst = out_dir / f"{m.media_id}_{src.stem}_8bit{ext}"
        console.print(f"转码 {m.filename} ({m.pix_fmt}) -> {dst.name} ...")
        transcode_for_davinci_free(src, dst, codec=codec)
        new_rel = dst.relative_to(paths.root).as_posix()
        new_abs = str(dst.resolve())
        m.rel_path = new_rel
        m.abs_path = new_abs
        m.is_10bit = False
        m.pix_fmt = "yuv420p" if codec == "h264_8bit" else "yuv422p10le(prores)"
        m.davinci_free_warning = None
        remapped[m.media_id] = (new_rel, new_abs)

    media_index.has_10bit_media = False
    paths.media_index_json.write_text(
        json.dumps(media_index.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for plan_path in (paths.rough_cut_yaml, paths.fine_cut_yaml):
        if plan_path.exists():
            ep = load_edit_plan(plan_path)
            for item in ep.items:
                if item.media_id in remapped:
                    item.source_rel_path, item.source_abs_path = remapped[item.media_id]
            save_edit_plan(plan_path, ep)

    console.print(f"[green]已转换 {len(ten_bit)} 个素材并更新索引与剪辑单，重新执行 `castcut export` 即可导入达芬奇。[/green]")


@app.command()
def run(
    episode_dir: Annotated[Path, typer.Argument(help="单集工作目录")],
    asr: Annotated[str, typer.Option(help="转写模式：auto / faster-whisper / none")] = "auto",
    formats: Annotated[str, typer.Option(help="导出格式：otio,fcpxml,srt,jianying")] = "otio,fcpxml,srt",
    burn_subs: Annotated[bool, typer.Option("--burn-subs/--no-burn-subs", help="精剪预览片是否烧录字幕")] = False,
) -> None:
    """一键跑通全流程：ingest -> rough plan -> fine plan -> render -> export -> qc。"""
    console.print("[bold cyan]1/5 Ingest 素材分析与挑片初始化...[/bold cyan]")
    ingest_episode(episode_dir, asr_mode=asr)
    console.print("[bold cyan]2/5 生成粗剪与精剪剪辑单...[/bold cyan]")
    build_rough_plan(episode_dir)
    build_fine_plan(episode_dir)
    console.print("[bold cyan]3/5 渲染精剪预览成片...[/bold cyan]")
    preview = render_episode(episode_dir, stage="fine", burn_subtitles=burn_subs)
    console.print("[bold cyan]4/5 导出 NLE 工程与字幕...[/bold cyan]")
    exported = export_episode(episode_dir, formats=[f.strip() for f in formats.split(",") if f.strip()])
    console.print("[bold cyan]5/5 运行自动质检...[/bold cyan]")
    report = run_qc(episode_dir, stage="fine")
    console.print(f"[green]全流程完成！[/green] 预览片: {preview}")
    for k, p in exported.items():
        console.print(f"  - {k.upper()}: {p}")
    console.print(f"  - QC: {'PASS' if report.passed else 'FAIL'} ({EpisodePaths(episode_dir).qc_report_json})")
    if not report.passed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
