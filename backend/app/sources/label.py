"""Package label adapters: text read from a photo (OCR) or typed in by the customer."""

from __future__ import annotations

from datetime import datetime

from app.engine.types import EvidenceSource, SourceType
from app.packaged.ocr import IngredientSection, OcrResult
from app.sources.base import AdapterStatus, AdapterResult, SourceAdapter, register


@register
class LabelPhotoAdapter(SourceAdapter):
    name = "label_photo"
    source_type = SourceType.label_ocr
    description = "Ingredient list read from the customer's label photo with Tesseract OCR."

    def convert(self, section: IngredientSection, ocr: OcrResult, photo_index: int, now: datetime) -> AdapterResult:
        def go():
            if not section.ingredients_text.strip():
                return AdapterStatus.not_found, [], None, "No ingredient text."
            src = EvidenceSource(
                id="label",
                type=SourceType.label_ocr,
                name="Package label photo",
                ingredients_text=section.ingredients_text,
                complete=section.found_heading,
                confidence=ocr.confidence,
                reference=f"label photo {photo_index + 1}",
                verification="label",
                as_of=now,
            )
            return AdapterStatus.ok, [src], None, f"Read text from photo {photo_index + 1}."
        return self.run(go)


@register
class LabelTextAdapter(SourceAdapter):
    name = "label_text"
    source_type = SourceType.label_manual
    description = "Ingredient list typed in by the customer from the package label."

    def convert(self, ingredients_text: str, may_contain_text: str, product_name: str, now: datetime) -> AdapterResult:
        def go():
            src = EvidenceSource(
                id="manual",
                type=SourceType.label_manual,
                name=product_name or "Ingredient list you entered",
                ingredients_text=ingredients_text,
                cross_contact_text=may_contain_text or None,
                reference="label text typed by the customer",
                verification="label",
                as_of=now,
            )
            return AdapterStatus.ok, [src], None, "Typed label text."
        return self.run(go)
