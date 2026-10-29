"""Barcode detection (EAN/UPC) from product photos."""

from __future__ import annotations

import re

from PIL import Image


def valid_gtin(code: str) -> bool:
    """EAN-8, UPC-A (12), EAN-13 or GTIN-14 with a correct check digit."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", code):
        return False
    digits = [int(d) for d in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_barcode(code: str) -> str | None:
    code = re.sub(r"\D", "", code or "")
    return code if valid_gtin(code) else None


def read_barcodes(img: Image.Image) -> list[str]:
    try:
        import zxingcpp
    except ImportError:
        return []
    found: list[str] = []
    for result in zxingcpp.read_barcodes(img):
        code = normalize_barcode(result.text)
        if code and code not in found:
            found.append(code)
    return found
