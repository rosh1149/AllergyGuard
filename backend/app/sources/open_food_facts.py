"""Open Food Facts adapter: barcode lookup and name search."""

from __future__ import annotations

from app.core.config import get_settings
from app.engine.types import CrossContactDecl, EvidenceSource, SourceType
from app.packaged.off import OffMalformed, OffProduct, OffRateLimited, OffUnavailable, OpenFoodFactsClient
from app.sources.base import AdapterError, AdapterResult, AdapterStatus, SourceAdapter, register


def _call(fn):
    try:
        return fn()
    except OffRateLimited as e:
        raise AdapterError(AdapterStatus.rate_limited, str(e)) from e
    except OffMalformed as e:
        raise AdapterError(AdapterStatus.malformed, str(e)) from e
    except OffUnavailable as e:
        raise AdapterError(AdapterStatus.unavailable, str(e)) from e


@register
class OpenFoodFactsAdapter(SourceAdapter):
    name = "open_food_facts"
    source_type = SourceType.product_database
    description = "Open Food Facts product database (community-maintained): barcode lookup and name search."

    def __init__(self, client: OpenFoodFactsClient | None):
        self.client = client

    @staticmethod
    def to_evidence(p: OffProduct) -> EvidenceSource | None:
        if not (p.ingredients_text or p.allergen_codes or p.trace_codes):
            return None
        return EvidenceSource(
            id=f"off:{p.barcode}",
            type=SourceType.product_database,
            name=f"Open Food Facts: {p.display_name or p.barcode}",
            ingredients_text=p.ingredients_text or None,
            declared_allergens=p.allergen_codes,
            cross_contact=[
                CrossContactDecl(c, "possible", "Listed as traces", kind="precautionary_label", status="reported")
                for c in p.trace_codes
            ],
            complete=bool(p.ingredients_text),
            url=p.url,
            note="Community-maintained database; may be out of date.",
            reference=f"barcode {p.barcode}",
            verification="community",
            as_of=p.updated_at,
            max_age_days=get_settings().product_record_max_age_days,
        )

    def lookup(self, barcode: str) -> AdapterResult:
        def go():
            if self.client is None:
                return AdapterStatus.skipped, [], None, "Product database lookup is disabled."
            p = _call(lambda: self.client.get_product(barcode))
            if p is None:
                return AdapterStatus.not_found, [], None, f"Barcode {barcode} not found."
            src = self.to_evidence(p)
            return AdapterStatus.ok, [src] if src else [], p, f"Found barcode {barcode}."
        return self.run(go)

    def search(self, query: str) -> AdapterResult:
        """Candidate products for a name. Used for identification only, so no evidence is returned."""
        def go():
            if self.client is None:
                return AdapterStatus.skipped, [], [], "Product database lookup is disabled."
            found = _call(lambda: self.client.search(query))
            return (AdapterStatus.ok if found else AdapterStatus.not_found), [], found, f"{len(found)} result(s)."
        return self.run(go)
