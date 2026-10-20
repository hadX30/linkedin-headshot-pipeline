"""
Image handling: validate and normalise uploads, and draw mock headshots.

Every upload is decoded with Pillow (so the real file type is checked, not the
file name), rotated upright from EXIF, converted to RGB, downscaled to a size
the model APIs accept, and re-encoded as JPEG. Both the Claude stages and the
image model then receive the same, small, correctly-typed file.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}
STUDIO_GREY = (232, 230, 227)  # #E8E6E3, the backdrop the edit prompt asks for


class InvalidImage(ValueError):
    """The upload is not a supported, decodable image."""


class ImageTooSmall(ValueError):
    """The upload decodes, but is below the minimum usable size."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes  # JPEG
    width: int
    height: int
    source_format: str
    source_width: int
    source_height: int
    source_bytes: int
    sha256: str

    def meta(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "bytes": len(self.data),
            "source_format": self.source_format,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "source_bytes": self.source_bytes,
            "sha256": self.sha256,
        }


def normalize_upload(raw: bytes, *, max_edge: int, min_edge: int) -> NormalizedImage:
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            source_format = probe.format or "UNKNOWN"
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImage("The file is not a readable image.") from exc

    if source_format not in ACCEPTED_FORMATS:
        raise InvalidImage(f"Unsupported image type {source_format}. Upload a JPEG, PNG or WebP photo.")

    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        source_width, source_height = img.size
        if min(img.size) < min_edge:
            raise ImageTooSmall(
                f"The photo is {source_width}x{source_height}px; the shorter side must be at least {min_edge}px."
            )
        img = img.convert("RGB")
        if max(img.size) > max_edge:
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92, optimize=True)  # drops EXIF, incl. GPS
        width, height = img.size

    data = out.getvalue()
    return NormalizedImage(
        data=data,
        width=width,
        height=height,
        source_format=source_format,
        source_width=source_width,
        source_height=source_height,
        source_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def render_mock_headshot(source_jpeg: bytes, *, attempt: int, label: str, size: int = 1024) -> bytes:
    """
    Make a free stand-in for the image model's output: a square crop of the
    upload, warmed slightly, with its edges faded into the studio grey, and a
    visible MOCK tag so it can never be mistaken for a real result.
    """
    with Image.open(io.BytesIO(source_jpeg)) as img:
        img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = int((h - side) * 0.2)  # faces sit in the upper part of portraits
        img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)

    # Warm grade
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.04)))
    b = b.point(lambda v: int(v * 0.96))
    img = Image.merge("RGB", (r, g, b))

    # Fade edges into the backdrop colour with a soft elliptical mask
    mask = Image.new("L", (size, size), 0)
    inset = int(size * 0.1)
    ImageDraw.Draw(mask).ellipse((inset, inset // 2, size - inset, size + inset), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size // 10))
    img = Image.composite(img, Image.new("RGB", (size, size), STUDIO_GREY), mask)

    # MOCK tag
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
