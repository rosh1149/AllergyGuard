"""OCR of ingredient labels (Tesseract) and extraction of the ingredient statement."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from PIL import Image

from app.core.config import get_settings
from app.packaged.images import prepare_for_ocr

log = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    confidence: float  # 0-1, mean word confidence
    available: bool = True


def run_ocr(img: Image.Image) -> OcrResult:
    try:
        import pytesseract
    except ImportError:
        return OcrResult("", 0.0, available=False)
    cmd = get_settings().tesseract_cmd
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        data = pytesseract.image_to_data(
            prepare_for_ocr(img), config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, OSError) as e:
        log.warning("OCR unavailable: %s", e)
        return OcrResult("", 0.0, available=False)

    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    confidence = (sum(confs) / len(confs) / 100) if confs else 0.0
    return OcrResult(text, round(confidence, 3))


# Tolerates common OCR misreads of the first letter ("{NGREDIENTS", "lNGREDIENTS", "|ngredients").
_START = re.compile(r"(?:\b|(?<=[\s{(\[|]))[{(\[|il1]?(ngredients?|ngred[il1]ents?)\b\s*[:.;-]?|\bingr\.\s*", re.I)
# Things that follow the ingredient statement on a label.
_END = re.compile(
    r"\b(nutrition(al)?\s+(information|facts|values?)|typical\s+values|per\s+100\s*g|energy\s*[:\d]|"
    r"storage|store\s+in|keep\s+(refrigerated|in\s+a)|best\s+before|use\s+by|net\s+(wt|weight)|"
    r"manufactured\s+by|distributed\s+by|produced\s+by|packed\s+by|serving\s+suggestion|"
    r"directions|preparation|cooking\s+instructions)\b",
    re.I,
)
_ALLERGY_ADVICE = re.compile(r"\ballergy\s+(advice|information)\s*[:\-]?", re.I)


@dataclass
class IngredientSection:
    ingredients_text: str
    found_heading: bool  # an "Ingredients" heading was found (the statement is likely complete)


def extract_ingredient_section(text: str) -> IngredientSection | None:
    """Pull the ingredient statement (and any allergy advice after it) out of full label text."""
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    flat = re.sub(r"(\w)- (\w)", r"\1\2", flat)  # re-join words hyphenated across lines
    if not flat:
        return None
    start = _START.search(flat)
    if start is None:
        return IngredientSection(flat, found_heading=False)
    body = flat[start.end():]
    end = _END.search(body)
    if end:
        body = body[: end.start()]
    body = _ALLERGY_ADVICE.sub(" ", body)
    body = body.strip(" .:;")
    if len(body) < 3:
        return None
    return IngredientSection(body, found_heading=True)
