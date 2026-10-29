"""Packaged-food pipeline: photos -> identification + label OCR + product data -> shared engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.engine.service import analyze_for_user
from app.engine.types import (
    MIN_IDENTITY_CONFIDENCE,
    AnalysisContext,
    AnalysisResult,
    CrossContactDecl,
    EvidenceSource,
    SourceType,
)
from app.models import User
from app.packaged.barcode import normalize_barcode, read_barcodes
from app.packaged.images import LoadedImage
from app.packaged.ocr import IngredientSection, OcrResult, extract_ingredient_section, run_ocr
from app.packaged.off import OffProduct, OffUnavailable, OpenFoodFactsClient, name_similarity
from app.packaged.vision import ProductGuess, identify_product

log = logging.getLogger(__name__)

BARCODE_CONFIDENCE = 0.95
MIN_SEARCH_SIMILARITY = 0.5


class ProductInfo(BaseModel):
    barcode: str = ""
    name: str = ""
    brand: str = ""
    quantity: str = ""
    image_url: str = ""
    source_url: str = ""
    identification_method: str  # barcode | image_recognition | name_search
    identity_confidence: float
    confident: bool


class LabelInfo(BaseModel):
    ocr_available: bool
    ingredients_found: bool
    heading_found: bool
    ingredients_text: str = ""
    confidence: float = 0.0
    from_image: int | None = None


class ImageInfo(BaseModel):
    width: int
    height: int
    sharpness: float
    brightness: float
    issues: list[str]


class Step(BaseModel):
    name: str
    status: str  # done | skipped | failed
    detail: str = ""


class PackagedAnalysisOut(BaseModel):
    result: AnalysisResult
    product: ProductInfo | None
    label: LabelInfo
    images: list[ImageInfo]
    steps: list[Step]


@dataclass
class _Identity:
    product: OffProduct | None = None
    method: str = ""
    confidence: float = 0.0
    guess_label: str = ""
    limitations: list[str] = field(default_factory=list)


def _best_match(off: OpenFoodFactsClient, query: str) -> tuple[OffProduct | None, float]:
    best, best_sim = None, 0.0
    for p in off.search(query):
        sim = max(name_similarity(query, p.display_name), name_similarity(query, f"{p.brand} {p.name}"))
        if sim > best_sim:
            best, best_sim = p, sim
    return (best, best_sim) if best_sim >= MIN_SEARCH_SIMILARITY else (None, best_sim)


def _identify(
    off: OpenFoodFactsClient | None,
    barcodes: list[str],
    guess: ProductGuess | None,
    name_hint: str,
    steps: list[Step],
) -> _Identity:
    ident = _Identity()
    if off is None:
        steps.append(Step(name="product_lookup", status="skipped", detail="Product database lookup is disabled."))
        return ident
    try:
        for code in barcodes:
            product = off.get_product(code)
            if product:
                ident.product, ident.method, ident.confidence = product, "barcode", BARCODE_CONFIDENCE
                steps.append(Step(name="product_lookup", status="done", detail=f"Found barcode {code}."))
                return ident
        if barcodes:
            ident.limitations.append(
                f"Barcode {', '.join(barcodes)} was not found in the product database."
            )

        query, base_conf, method = "", 0.0, ""
        if guess and guess.is_packaged_food and (guess.brand or guess.product_name):
            query = " ".join(p for p in (guess.brand, guess.product_name, guess.variant) if p)
            base_conf, method = guess.confidence, "image_recognition"
            ident.guess_label = " ".join(p for p in (query, guess.size) if p)
        elif name_hint.strip():
            query, base_conf, method = name_hint.strip(), 0.9, "name_search"
            ident.guess_label = query
        if not query:
            steps.append(Step(name="product_lookup", status="skipped", detail="No barcode or product name to look up."))
            return ident

        product, sim = _best_match(off, query)
        if product is None:
            ident.limitations.append(f"No product database entry matched '{query}'.")
            steps.append(Step(name="product_lookup", status="done", detail=f"No match for '{query}'."))
            return ident
        ident.product, ident.method = product, method
        ident.confidence = round(base_conf * sim, 2)
        steps.append(
            Step(name="product_lookup", status="done", detail=f"Best match for '{query}': {product.display_name}.")
        )
    except OffUnavailable as e:
        log.warning("Open Food Facts unavailable: %s", e)
        ident.limitations.append("The product database could not be reached, so only the label photo was used.")
        steps.append(Step(name="product_lookup", status="failed", detail="Product database unavailable."))
    return ident


def _pick_label(ocr_results: list[tuple[int, OcrResult]]) -> tuple[int, OcrResult, IngredientSection] | None:
    candidates = []
    for idx, ocr in ocr_results:
        section = extract_ingredient_section(ocr.text) if ocr.text else None
        if section:
            candidates.append((idx, ocr, section))
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c[2].found_heading, c[1].confidence, len(c[2].ingredients_text)))


def run_packaged_pipeline(
    db: Session,
    user: User,
    images: list[LoadedImage],
    barcode: str = "",
    product_name: str = "",
    off: OpenFoodFactsClient | None = None,
    use_vision: bool = True,
) -> PackagedAnalysisOut:
    steps: list[Step] = []
    limitations: list[str] = []
    for i, img in enumerate(images, 1):
        for issue in img.issues:
            limitations.append(f"Photo {i}: {issue}" if len(images) > 1 else issue)

    # 1. Barcode
    barcodes: list[str] = []
    if barcode:
        code = normalize_barcode(barcode)
        if code:
            barcodes.append(code)
        else:
            limitations.append("The barcode entered is not a valid EAN/UPC number and was ignored.")
    for img in images:
        barcodes.extend(c for c in read_barcodes(img.image) if c not in barcodes)
    steps.append(
        Step(
            name="barcode",
            status="done" if barcodes else "skipped",
            detail=", ".join(barcodes) if barcodes else "No barcode found.",
        )
    )

    # 2. Label OCR
    ocr_results = [(i, run_ocr(img.image)) for i, img in enumerate(images)]
    ocr_available = bool(ocr_results) and all(o.available for _, o in ocr_results)
    picked = _pick_label(ocr_results)
    if images and not ocr_available:
        limitations.append("Text recognition is not available on this server; the label could not be read.")
        steps.append(Step(name="ocr", status="failed", detail="OCR engine unavailable."))
    elif picked:
        steps.append(Step(name="ocr", status="done", detail=f"Read text from photo {picked[0] + 1}."))
    elif images:
        limitations.append("No ingredient list could be read from the photos. Try a clear photo of the ingredients panel.")
        steps.append(Step(name="ocr", status="done", detail="No ingredient text found."))
    else:
        steps.append(Step(name="ocr", status="skipped", detail="No photos provided."))

    # 3. Identification (barcode -> vision -> name)
    guess = None
    if not barcodes and images and use_vision:
        guess = identify_product([img.image for img in images])
        steps.append(
            Step(
                name="image_recognition",
                status="done" if guess else "skipped",
                detail=(f"{guess.brand} {guess.product_name}".strip() or "Not recognised")
                if guess
                else "Image recognition is not configured.",
            )
        )
        if guess:
            barcodes.extend(c for c in [normalize_barcode(guess.barcode_digits)] if c)
    ident = _identify(off, barcodes, guess, product_name, steps)
    limitations.extend(ident.limitations)

    # 4. Evidence
    sources: list[EvidenceSource] = []
    label = LabelInfo(ocr_available=ocr_available, ingredients_found=False, heading_found=False)
    if picked:
        idx, ocr, section = picked
        label = LabelInfo(
            ocr_available=True,
            ingredients_found=True,
            heading_found=section.found_heading,
            ingredients_text=section.ingredients_text,
            confidence=ocr.confidence,
            from_image=idx,
        )
        if not section.found_heading:
            limitations.append(
                "No 'Ingredients' heading was found in the photo; the text read may not be the full ingredient list."
            )
        sources.append(
            EvidenceSource(
                id="label",
                type=SourceType.label_ocr,
                name="Package label photo",
                ingredients_text=section.ingredients_text,
                complete=section.found_heading,
                confidence=ocr.confidence,
            )
        )

    product_info = None
    p = ident.product
    if p:
        product_info = ProductInfo(
            barcode=p.barcode,
            name=p.name,
            brand=p.brand,
            quantity=p.quantity,
            image_url=p.image_url,
            source_url=p.url,
            identification_method=ident.method,
            identity_confidence=ident.confidence,
            confident=ident.confidence >= MIN_IDENTITY_CONFIDENCE,
        )
        if p.ingredients_text or p.allergen_codes or p.trace_codes:
            sources.append(
                EvidenceSource(
                    id=f"off:{p.barcode}",
                    type=SourceType.product_database,
                    name=f"Open Food Facts: {p.display_name or p.barcode}",
                    ingredients_text=p.ingredients_text or None,
                    declared_allergens=p.allergen_codes,
                    cross_contact=[CrossContactDecl(c, "possible", "Listed as traces") for c in p.trace_codes],
                    complete=bool(p.ingredients_text),
                    url=p.url,
                    note="Community-maintained database; may be out of date.",
                )
            )
            limitations.append(
                "Product database entries are community-maintained and may not match the current recipe."
            )
        else:
            limitations.append("The product database entry has no ingredient information.")

    if p and ident.confidence >= MIN_IDENTITY_CONFIDENCE:
        identity_label = p.display_name
    elif p:
        identity_label = f"{ident.guess_label or p.display_name} (possible match: {p.display_name})"
    else:
        identity_label = ident.guess_label or product_name.strip()

    ctx = AnalysisContext(
        identity_confidence=ident.confidence if p else None,
        identity_label=identity_label,
        limitations=limitations,
    )
    result = analyze_for_user(db, user, sources, ctx)
    steps.append(Step(name="analysis", status="done", detail=result.overall_label))

    return PackagedAnalysisOut(
        result=result,
        product=product_info,
        label=label,
        images=[ImageInfo(width=i.width, height=i.height, sharpness=i.sharpness, brightness=i.brightness, issues=i.issues) for i in images],
        steps=steps,
    )
