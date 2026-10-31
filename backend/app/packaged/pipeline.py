"""Packaged-food pipeline: photos -> identification + label OCR + product data -> shared engine."""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from contextlib import nullcontext
from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.engine.service import AssessmentLink, analyze_for_user
from app.core.limits import image_job
from app.core.timing import StageTimer
from app.models.common import utcnow
from app.packaged.snapshots import record_page, record_snapshot, saved_copy
from app.sources.web import ManufacturerPageAdapter, ProductIdentity, WebSearchAdapter, search_provider
from app.engine.types import MIN_IDENTITY_CONFIDENCE, MIN_SOURCE_CONFIDENCE, AnalysisContext, AnalysisResult, EvidenceSource
from app.models import ManufacturerSite, ReviewKind
from app.review.queue import ReviewFlag, queue_flags_later
from app.models import User
from app.packaged.barcode import normalize_barcode, read_barcodes
from app.packaged.images import LoadedImage
from app.packaged.ocr import IngredientSection, OcrResult, extract_ingredient_section, run_ocr
from app.packaged.off import OffProduct, OpenFoodFactsClient, name_similarity
from app.packaged.vision import ProductGuess, identify_product
from app.sources.base import AdapterStatus
from app.sources.label import LabelPhotoAdapter
from app.sources.open_food_facts import OpenFoodFactsAdapter

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
    duration_ms: int | None = None


class PackagedAnalysisOut(BaseModel):
    result: AnalysisResult
    product: ProductInfo | None
    label: LabelInfo
    images: list[ImageInfo]
    steps: list[Step]
    # Other likely products when the name/photo match was ambiguous, so the app can ask which one it is.
    product_candidates: list["ProductCandidate"] = []
    needs_confirmation: bool = False


class ProductCandidate(BaseModel):
    barcode: str
    name: str
    similarity: float


@dataclass
class _Identity:
    product: OffProduct | None = None
    method: str = ""
    confidence: float = 0.0
    guess_label: str = ""
    limitations: list[str] = field(default_factory=list)
    candidates: list[ProductCandidate] = field(default_factory=list)
    ambiguous: bool = False  # several products matched about equally well
    query: str = ""
    saved_copy_from: datetime | None = None  # set when a saved copy replaced an unreachable database


FAILURE_MESSAGES = {
    AdapterStatus.rate_limited: "The product database is busy (too many requests), so it was not used. Try again in a minute.",
    AdapterStatus.malformed: "The product database returned data that could not be read, so it was not used.",
    AdapterStatus.unavailable: "The product database could not be reached, so only the label photo was used.",
}
AMBIGUITY_MARGIN = 0.1  # top two name matches closer than this: ask the customer which one it is
AMBIGUOUS_CONFIDENCE_CAP = 0.6  # an ambiguous match never counts as a confident identification


def _ranked(products: list[OffProduct], query: str) -> list[tuple[OffProduct, float]]:
    scored = {}
    for p in products:
        sim = max(name_similarity(query, p.display_name), name_similarity(query, f"{p.brand} {p.name}"))
        if p.barcode not in scored or sim > scored[p.barcode][1]:
            scored[p.barcode] = (p, sim)
    return sorted(scored.values(), key=lambda x: -x[1])


def _failed(ident: _Identity, steps: list[Step], result) -> _Identity:
    log.warning("Product database adapter failed (%s): %s", result.status.value, result.detail)
    ident.limitations.append(FAILURE_MESSAGES[result.status])
    steps.append(Step(name="product_lookup", status="failed", detail=f"Product database {result.status.value.replace('_', ' ')}."))
    return ident


def _identify(
    adapter: OpenFoodFactsAdapter,
    barcodes: list[str],
    guess: ProductGuess | None,
    name_hint: str,
    steps: list[Step],
    fallback: Callable[[str], tuple[OffProduct, object] | None] | None = None,
) -> _Identity:
    ident = _Identity()
    if adapter.client is None:
        steps.append(Step(name="product_lookup", status="skipped", detail="Product database lookup is disabled."))
        return ident
    for code in barcodes:
        r = adapter.lookup(code)
        if r.status.failed:
            saved = fallback(code) if fallback else None
            if saved is None:
                return _failed(ident, steps, r)
            product, snap = saved
            ident.product, ident.method, ident.confidence = product, "barcode", BARCODE_CONFIDENCE
            ident.saved_copy_from = snap.last_seen
            ident.limitations.append(
                f"The product database could not be used ({r.status.value.replace('_', ' ')}), so a copy of its "
                f"record saved on {snap.last_seen:%Y-%m-%d} was used."
            )
            steps.append(Step(name="product_lookup", status="done", detail=f"Used saved copy for barcode {code}."))
            return ident
        if r.ok:
            ident.product, ident.method, ident.confidence = r.payload, "barcode", BARCODE_CONFIDENCE
            steps.append(Step(name="product_lookup", status="done", detail=r.detail))
            return ident
    if barcodes:
        ident.limitations.append(f"Barcode {', '.join(barcodes)} was not found in the product database.")

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
    ident.query = query

    r = adapter.search(query)
    if r.status.failed:
        return _failed(ident, steps, r)
    ranked = [(p, sim) for p, sim in _ranked(r.payload or [], query) if sim >= MIN_SEARCH_SIMILARITY]
    if not ranked:
        ident.limitations.append(f"No product database entry matched '{query}'.")
        steps.append(Step(name="product_lookup", status="done", detail=f"No match for '{query}'."))
        return ident
    product, sim = ranked[0]
    ident.product, ident.method = product, method
    ident.confidence = round(base_conf * sim, 2)
    ident.candidates = [ProductCandidate(barcode=p.barcode, name=p.display_name or p.barcode, similarity=round(s, 2))
                        for p, s in ranked[:3]]
    if len(ranked) > 1 and sim - ranked[1][1] < AMBIGUITY_MARGIN:
        ident.ambiguous = True
        ident.confidence = min(ident.confidence, AMBIGUOUS_CONFIDENCE_CAP)
        ident.limitations.append(
            f"Several products match '{query}' about equally well ("
            + ", ".join(c.name for c in ident.candidates)
            + "). Choose yours or scan the barcode; until then the product database is not treated as reliable."
        )
    steps.append(Step(name="product_lookup", status="done", detail=f"Best match for '{query}': {product.display_name}."))
    return ident


def _review_flags(ident: _Identity, picked, images, ocr_available: bool, identity_label: str) -> list[ReviewFlag]:
    """Uncertain input worth a person's look. Only text and scores are kept, never the photos."""
    flags: list[ReviewFlag] = []
    p = ident.product
    product_key = (p.barcode if p and ident.method == "barcode" else ident.query) or identity_label or "unidentified product"
    if ident.ambiguous:
        flags.append(ReviewFlag(
            ReviewKind.multiple_product_matches, ident.query,
            f"Several products match '{ident.query}'.",
            {"candidates": [c.model_dump() for c in ident.candidates]},
        ))
    elif p and ident.confidence < MIN_IDENTITY_CONFIDENCE:
        flags.append(ReviewFlag(
            ReviewKind.low_confidence_product, product_key,
            f"'{ident.query or p.barcode}' was matched to {p.display_name} with low confidence.",
            {"method": ident.method, "confidence": ident.confidence, "matched_barcode": p.barcode,
             "matched_name": p.display_name},
        ))
    if images and ocr_available:
        issues = sorted({i for img in images for i in img.issues})
        if picked is None:
            flags.append(ReviewFlag(ReviewKind.unreadable_label, product_key,
                                    f"No ingredient list could be read for {product_key}.", {"image_issues": issues}))
        else:
            _, ocr, section = picked
            if ocr.confidence < MIN_SOURCE_CONFIDENCE:
                flags.append(ReviewFlag(ReviewKind.unreadable_label, product_key,
                                        f"The label for {product_key} was read with low confidence.",
                                        {"ocr_confidence": round(ocr.confidence, 2), "text": section.ingredients_text[:1000],
                                         "image_issues": issues}))
            elif not section.found_heading:
                flags.append(ReviewFlag(ReviewKind.incomplete_label, product_key,
                                        f"No 'Ingredients' heading was found on the label for {product_key}.",
                                        {"text": section.ingredients_text[:1000]}))
    return flags


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
    web: httpx.Client | None = None,
) -> PackagedAnalysisOut:
    steps: list[Step] = []
    limitations: list[str] = []
    now = utcnow()
    timer = StageTimer()
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
    with image_job() if images else nullcontext():  # CPU-heavy photo work is limited server-wide
        with timer("barcode"):
            for img in images:
                barcodes.extend(c for c in read_barcodes(img.image) if c not in barcodes)
        with timer("ocr"):
            ocr_results = [(i, run_ocr(img.image)) for i, img in enumerate(images)]
    steps.append(
        Step(
            name="barcode",
            status="done" if barcodes else "skipped",
            detail=", ".join(barcodes) if barcodes else "No barcode found.",
        )
    )

    # 2. Label OCR (read above, inside the photo-processing slot)
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
        with timer("image_recognition"):
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
    with timer("product_lookup"):
        ident = _identify(OpenFoodFactsAdapter(off), barcodes, guess, product_name, steps,
                          fallback=lambda code: saved_copy(db, code))
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
        sources.extend(LabelPhotoAdapter().convert(section, ocr, idx, now).sources)

    product_info = None
    snapshot_id = None
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
            snap, changes = record_snapshot(db, p)
            snapshot_id = snap.id
            if changes:
                limitations.append(
                    "This product's database record has changed since we last saw it ("
                    + "; ".join(changes) + "). The product may have been reformulated: check the package label."
                )
            src = OpenFoodFactsAdapter.to_evidence(p)
            if ident.saved_copy_from:
                src.reference += f" (saved copy from {ident.saved_copy_from:%Y-%m-%d})"
            sources.append(src)
            limitations.append(
                "Product database entries are community-maintained and may not match the current recipe."
            )
        else:
            limitations.append("The product database entry has no ingredient information.")

    # 5. External pages: the manufacturer's site (approved domains) and web search, if configured.
    identity = ProductIdentity(brand=p.brand if p else (guess.brand if guess else ""),
                               name=p.name if p else (guess.product_name if guess else product_name.strip()),
                               barcode=p.barcode if p else (barcodes[0] if barcodes else ""))
    if web is not None and identity.brand and identity.name:
        sites = [(m.brand, m.domain, m.product_url_template) for m in db.scalars(select(ManufacturerSite)).all()]
        with timer("external_pages"):
            mfr = ManufacturerPageAdapter(web, sites).fetch(identity)
            found = list(mfr.sources)
            rejected = list((mfr.payload or {}).get("rejected", [])) if isinstance(mfr.payload, dict) else []
            if not found:
                search = WebSearchAdapter(web, search_provider(), sites).fetch(identity)
                found += search.sources
                if isinstance(search.payload, dict):
                    rejected += search.payload.get("rejected", [])
        for src in found:
            sources.append(src)
            record_page(db, identity, src)
        for url, why in rejected[:3]:
            limitations.append(f"A page was not used because it may describe a different product ({why}): {url}")
        failed = [r for r in (mfr,) if r.status.failed]
        steps.append(Step(
            name="external_pages", status="failed" if failed and not found else ("done" if found else "skipped"),
            detail=(f"{len(found)} page(s) used" + (f", {len(rejected)} rejected" if rejected else "")) if (found or rejected)
            else mfr.detail,
        ))
        if failed and not found:
            limitations.append("The manufacturer's website could not be reached.")

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
        stages=timer.stages(steps),
        started=timer.started,
    )
    result = analyze_for_user(
        db, user, sources, ctx,
        record=AssessmentLink(kind="packaged", barcode=p.barcode if p else None, product_snapshot_id=snapshot_id),
    )
    queue_flags_later(_review_flags(ident, picked, images, ocr_available, identity_label), result.assessment_id)
    steps.append(Step(name="analysis", status="done", detail=result.overall_label))

    return PackagedAnalysisOut(
        result=result,
        product=product_info,
        label=label,
        images=[ImageInfo(width=i.width, height=i.height, sharpness=i.sharpness, brightness=i.brightness, issues=i.issues) for i in images],
        steps=steps,
        product_candidates=ident.candidates if ident.ambiguous else [],
        needs_confirmation=ident.ambiguous,
    )
