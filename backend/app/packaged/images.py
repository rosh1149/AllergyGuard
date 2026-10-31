"""Image loading, validation and quality checks."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# A phone photo is 12-50 megapixels. Anything larger is refused before it is decoded, so a small
# compressed file cannot expand into gigabytes of memory (a "decompression bomb").
MAX_PIXELS = 60_000_000
MIN_SIDE = 300
BLUR_THRESHOLD = 60.0  # variance of the edge image; lower = blurrier
DARK_THRESHOLD = 70  # 99th-percentile brightness below this = too dark
MIN_CONTRAST = 80  # spread between 1st and 99th percentile brightness


class ImageError(ValueError):
    pass


@dataclass
class LoadedImage:
    image: Image.Image  # RGB, EXIF orientation applied
    format: str
    width: int
    height: int
    sharpness: float
    brightness: float
    issues: list[str] = field(default_factory=list)


def _percentiles(gray: Image.Image, *pcts: int) -> list[int]:
    hist = gray.histogram()
    total = sum(hist)
    out = []
    for pct in pcts:
        target, running = total * pct / 100, 0
        for value, count in enumerate(hist):
            running += count
            if running >= target:
                out.append(value)
                break
    return out


def load_image(data: bytes, max_bytes: int) -> LoadedImage:
    if not data:
        raise ImageError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise ImageError(f"The image is larger than {max_bytes // (1024 * 1024)} MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        fmt = img.format or ""
        if img.width * img.height > MAX_PIXELS:  # checked before decoding: a small file can unpack huge
            raise ImageError(f"The image is too large ({img.width}x{img.height} pixels).")
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageError("The file is not a readable image.") from e
    if fmt not in ALLOWED_FORMATS:
        raise ImageError("Only JPEG, PNG and WebP images are supported.")

    gray = img.convert("L")
    sample = gray.copy()
    sample.thumbnail((1000, 1000))
    edges = sample.filter(ImageFilter.FIND_EDGES)
    # FIND_EDGES marks the image border; crop it so it doesn't count as detail.
    edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    sharpness = ImageStat.Stat(edges).var[0]
    brightness = ImageStat.Stat(sample).mean[0]
    dark_end, light_end = _percentiles(sample, 1, 99)

    issues = []
    if min(img.size) < MIN_SIDE:
        issues.append("The image resolution is low.")
    if sharpness < BLUR_THRESHOLD:
        issues.append("The image looks blurry.")
    if light_end < DARK_THRESHOLD:
        issues.append("The image is very dark.")
    elif light_end - dark_end < MIN_CONTRAST:
        issues.append("The image has very low contrast (overexposed or washed out).")
    return LoadedImage(img, fmt, img.width, img.height, round(sharpness, 1), round(brightness, 1), issues)


def estimate_skew(gray: Image.Image, max_angle: float = 8.0, step: float = 0.5) -> float:
    """Text-line angle in degrees, by maximising the variance of row sums of dark pixels (projection
    profile) over small rotations of a downscaled copy. 0 when no clear text lines are found."""
    import numpy as np

    small = gray.copy()
    small.thumbnail((600, 600))
    ink = (np.asarray(ImageOps.autocontrast(small)) < 128).astype(np.uint8)
    if ink.mean() < 0.005:
        return 0.0
    base = Image.fromarray(ink * 255)
    best_angle, best_score = 0.0, -1.0
    angle = -max_angle
    while angle <= max_angle + 1e-9:
        rows = np.asarray(base.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0)).sum(axis=1)
        score = float(rows.astype(np.float64).var())
        if score > best_score:
            best_angle, best_score = angle, score
        angle += step
    return round(best_angle, 2)


def prepare_for_ocr(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    # Straighten slightly tilted photos: Tesseract misreads letters on skewed lines (Stage 3 OCR
    # evaluation: 4 degrees of tilt turned "INGREDIENTS" into "{NGREDIENTS" and "egg" into "299").
    angle = estimate_skew(gray)
    if abs(angle) >= 0.5:
        gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    # Tesseract works best with text around 30px tall; upscale small images.
    if max(gray.size) < 1600:
        factor = 1600 / max(gray.size)
        gray = gray.resize((int(gray.width * factor), int(gray.height * factor)), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(gray, cutoff=1)


def to_jpeg_bytes(img: Image.Image, max_side: int = 1568) -> bytes:
    copy = img.copy()
    copy.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
