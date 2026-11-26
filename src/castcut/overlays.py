"""Transparent PNG HUD card generator for lure fishing vlogs (Track V2 & FFmpeg preview)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from castcut.media import find_cjk_font
from castcut.models import EditPlan, HudOverlaySpec

ACCENT_COLORS = {
    "chapter": (46, 196, 182, 255),        # water teal
    "catch_counter": (255, 183, 3, 255),   # strike gold
    "lure_info": (56, 182, 255, 255),      # tackle sky blue
    "spot_note": (132, 204, 22, 255),      # reed green
    "callout": (249, 115, 22, 255),        # blooper orange
}


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = find_cjk_font(bold=bold)
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_overlay_png(
    spec: HudOverlaySpec,
    canvas_w: int,
    canvas_h: int,
    dest: Path,
) -> Path:
    """Render a full-canvas transparent RGBA PNG so both FFmpeg and DaVinci Resolve V2 place it identically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    scale = min(canvas_w, canvas_h) / 1080.0
    title_font = _load_font(max(22, int(40 * scale)), bold=True)
    sub_font = _load_font(max(16, int(24 * scale)), bold=False)

    t_bbox = draw.textbbox((0, 0), spec.title, font=title_font)
    t_w, t_h = t_bbox[2] - t_bbox[0], t_bbox[3] - t_bbox[1]

    s_w, s_h = 0, 0
    if spec.subtitle:
        s_bbox = draw.textbbox((0, 0), spec.subtitle, font=sub_font)
        s_w, s_h = s_bbox[2] - s_bbox[0], s_bbox[3] - s_bbox[1]

    pad_x = int(28 * scale)
    pad_y = int(18 * scale)
    bar_w = int(8 * scale)
    line_gap = int(8 * scale) if spec.subtitle else 0

    card_w = min(int(canvas_w * 0.78), max(t_w, s_w) + pad_x * 2 + bar_w + int(16 * scale))
    card_h = t_h + s_h + line_gap + pad_y * 2 + int(8 * scale)

    margin_x = int(canvas_w * 0.05)
    margin_y = int(canvas_h * 0.06)

    if spec.position == "top_right":
        x0 = canvas_w - margin_x - card_w
        y0 = margin_y
    elif spec.position == "bottom_left":
        x0 = margin_x
        y0 = canvas_h - int(canvas_h * 0.16) - card_h
    elif spec.position == "center_banner":
        x0 = (canvas_w - card_w) // 2
        y0 = int(canvas_h * 0.42)
    else:  # top_left
        x0 = margin_x
        y0 = margin_y

    x1 = x0 + card_w
    y1 = y0 + card_h
    radius = int(14 * scale)

    # Frosted dark card backdrop
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(14, 20, 28, 212))
    # Left accent stripe
    accent = ACCENT_COLORS.get(spec.kind, (46, 196, 182, 255))
    draw.rounded_rectangle(
        [x0 + int(10 * scale), y0 + int(12 * scale), x0 + int(10 * scale) + bar_w, y1 - int(12 * scale)],
        radius=max(2, bar_w // 2),
        fill=accent,
    )

    text_x = x0 + int(10 * scale) + bar_w + int(18 * scale)
    text_y = y0 + pad_y
    draw.text((text_x, text_y), spec.title, fill=(248, 250, 252, 255), font=title_font)
    if spec.subtitle:
        draw.text(
            (text_x, text_y + t_h + line_gap),
            spec.subtitle,
            fill=(203, 213, 225, 255),
            font=sub_font,
        )

    img.save(dest, format="PNG")
    return dest


def materialize_overlays(plan: EditPlan, episode_root: Path, overlays_dir: Path) -> list[Path]:
    """Render all HUD overlays referenced by plan into exports/overlays/ and record relative paths."""
    created: list[Path] = []
    for ov in plan.overlays:
        png_path = overlays_dir / f"{ov.overlay_id}_{ov.kind}.png"
        render_overlay_png(ov, plan.width, plan.height, png_path)
        ov.png_rel_path = png_path.relative_to(episode_root).as_posix()
        created.append(png_path)
    return created
