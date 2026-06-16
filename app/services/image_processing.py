"""Image processing for doctor signature and clinic logo uploads."""
from __future__ import annotations

import io

from PIL import Image, ImageOps


def _remove_light_background(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Make near-white pixels transparent — works for signatures on white paper."""
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r >= threshold and g >= threshold and b >= threshold:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def _fit_within(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    fitted = ImageOps.contain(img, (max_w, max_h), method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (max_w, max_h), (255, 255, 255, 0))
    offset_x = (max_w - fitted.width) // 2
    offset_y = (max_h - fitted.height) // 2
    canvas.paste(fitted, (offset_x, offset_y), fitted if fitted.mode == "RGBA" else None)
    return canvas


def process_signature_png(data: bytes) -> bytes:
    """Resize signature to max 400×150 and remove white background."""
    img = Image.open(io.BytesIO(data))
    img = _remove_light_background(img)
    img = _fit_within(img, 400, 150)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def process_letterhead_logo(data: bytes, max_width: int = 300) -> bytes:
    """Resize logo to max width (preserve aspect ratio), PNG output."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def process_logo_png(data: bytes) -> bytes:
    """Resize clinic logo to max 400×400 (square canvas, transparent padding)."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img = _fit_within(img, 400, 400)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
