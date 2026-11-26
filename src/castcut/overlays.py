"""Transparent PNG HUD card & watermark generator aligned with coolbot safe zones (3:4 & 16:9)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from castcut.media import find_cjk_font
from castcut.models import EditPlan, HudOverlaySpec

# Accent palette aligned with coolbot style-tokens.ts
ACCENT_COLORS = {
    "chapter": (2, 132, 199, 255),         # sky blue (#0284C7)
    "catch_counter": (250, 204, 21, 255),  # strike gold (#FACC15)
    "lure_info": (56, 189, 248, 255),      # tackle sky (#38BDF8)
    "spot_note": (22, 163, 74, 255),       # reed green (#16A34A)
    "callout": (249, 115, 22, 255),        # blooper orange (#F97316)
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
    """Render a full-canvas transparent RGBA PNG respecting coolbot 3:4 & 16:9 platform safe zones."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    scale = min(canvas_w, canvas_h) / 1080.0

    # Low-interference channel handle watermark (aligned with coolbot CHANNEL_HANDLE bottom-right corner)
    if spec.kind == "watermark":
        wm_font = _load_font(max(20, int(26 * scale)), bold=True)
        bbox = draw.textbbox((0, 0), spec.title, font=wm_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_r = int(28 * scale)
        pad_b = int(20 * scale)
        x = canvas_w - pad_r - tw
        y = canvas_h - pad_b - th
        # Soft drop shadow + translucent handle ink
        draw.text((x + 2, y + 2), spec.title, fill=(6, 16, 22, 150), font=wm_font)
        draw.text((x, y), spec.title, fill=(248, 250, 252, 185), font=wm_font)
        img.save(dest, format="PNG")
        return dest

    # Typography aligned with coolbot MIN_LABEL_PX=28, MIN_BODY_PX=34
    title_font = _load_font(max(28, int(40 * scale)), bold=True)
    sub_font = _load_font(max(24, int(28 * scale)), bold=False)

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

    # Safe margins aligned with coolbot visual.md:
    # - left: 48px
    # - lower-half right: 148px (clears Douyin like/comment/favorite column)
    # - bottom card clearance: 176px (clears 48px bottom margin + subtitle band)
    left_safe = int(48 * scale)
    right_upper_safe = int(48 * scale)
    right_lower_safe = int(148 * scale)
    top_safe = int(56 * scale)
    bottom_card_safe = int(176 * scale)

    is_lower = spec.position in ("bottom_left", "bottom_right", "center_banner")
    max_card_w = canvas_w - left_safe - (right_lower_safe if is_lower else right_upper_safe)
    card_w = min(max_card_w, max(t_w, s_w) + pad_x * 2 + bar_w + int(16 * scale))
    card_h = t_h + s_h + line_gap + pad_y * 2 + int(8 * scale)

    if spec.position == "top_right":
        x0 = canvas_w - right_upper_safe - card_w
        y0 = top_safe
    elif spec.position == "bottom_left":
        x0 = left_safe
        y0 = canvas_h - bottom_card_safe - card_h
    elif spec.position == "bottom_right":
        x0 = max(left_safe, canvas_w - right_lower_safe - card_w)
        y0 = canvas_h - bottom_card_safe - card_h
    elif spec.position == "center_banner":
        x0 = max(left_safe, (canvas_w - right_lower_safe + left_safe - card_w) // 2)
        y0 = int(canvas_h * 0.42)
    else:  # top_left
        x0 = left_safe
        y0 = top_safe

    x1 = x0 + card_w
    y1 = y0 + card_h
    radius = int(14 * scale)

    # Frosted dark card backdrop for outdoor footage legibility
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(14, 20, 28, 218))
    accent = ACCENT_COLORS.get(spec.kind, (2, 132, 199, 255))
    draw.rounded_rectangle(
        [
            x0 + int(10 * scale),
            y0 + int(12 * scale),
            x0 + int(10 * scale) + bar_w,
            y1 - int(12 * scale),
        ],
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
