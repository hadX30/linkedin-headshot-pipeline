"""
Image helpers.

Uploads are never re-encoded, resized or rotated: the model APIs receive the exact
bytes the user uploaded, with the media type taken from the file extension, as in
the original stages. Pillow is only used to *read* images (a validity check on the
new /v1/runs endpoint, metadata for traces) and to draw the mock-mode stand-in.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont, UnidentifiedImageError

# Same mapping (and the same image/jpeg fallback) as the original _load_image_as_base64.
MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
STUDIO_GREY = (232, 230, 227)  # #E8E6E3, the backdrop the edit prompt asks for


def media_type_for(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1]
    return MEDIA_TYPES.get(ext, "image/jpeg")


def extension_of(filename: str) -> str:
    """Lower-case extension with the dot, or '' when there is none."""
    name = filename.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return False


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def describe(data: bytes, filename: str) -> dict:
    """Metadata recorded with each run (read-only)."""
    meta = {"filename": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": media_type_for(filename)}
    try:
        with Image.open(io.BytesIO(data)) as img:
            meta.update({"width": img.size[0], "height": img.size[1], "format": img.format})
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        pass
    return meta


def render_mock_headshot(source: bytes, *, attempt: int, label: str, size: int = 1024) -> bytes:
    """
    Mock mode only: a free stand-in for the image model's output. A square crop of the
    upload, its edges faded into the studio grey, stamped MOCK so it can never be
    mistaken for a real result. Always a real PNG.
    """
    try:
        with Image.open(io.BytesIO(source)) as img:
            img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        img = Image.new("RGB", (size, size), (200, 200, 200))
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, int((h - side) * 0.2)
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    inset = int(size * 0.1)
    ImageDraw.Draw(mask).ellipse((inset, inset // 2, size - inset, size + inset), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size // 10))
    img = Image.composite(img, Image.new("RGB", (size, size), STUDIO_GREY), mask)

    draw = ImageDraw.Draw(img)
    text = f"MOCK · {label} · attempt {attempt}"
    font = ImageFont.load_default(size=size // 40)
    pad = size // 64
    box = draw.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x0, y0 = pad, size - th - pad * 3
    draw.rounded_rectangle((x0, y0, x0 + tw + pad * 2, y0 + th + pad * 2), radius=pad, fill=(20, 20, 20))
    draw.text((x0 + pad, y0 + pad - box[1]), text, fill=(255, 255, 255), font=font)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
