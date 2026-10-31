"""Open Food Facts client (https://openfoodfacts.github.io/openfoodfacts-server/api/)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.similarity import name_similarity  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

FIELDS = (
    "code,product_name,product_name_en,brands,quantity,ingredients_text,ingredients_text_en,"
    "allergens_tags,traces_tags,image_front_url,last_modified_t"
)

# Open Food Facts allergen tags -> AllergyGuard allergen codes.
OFF_ALLERGENS = {
    "en:gluten": "gluten",
    "en:crustaceans": "crustaceans",
    "en:eggs": "eggs",
    "en:fish": "fish",
    "en:peanuts": "peanuts",
    "en:soybeans": "soy",
    "en:milk": "milk",
    "en:nuts": "tree_nuts",
    "en:celery": "celery",
    "en:mustard": "mustard",
    "en:sesame-seeds": "sesame",
    "en:sulphur-dioxide-and-sulphites": "sulphites",
    "en:lupin": "lupin",
    "en:molluscs": "molluscs",
}


class OffUnavailable(Exception):
    """Open Food Facts could not be reached or returned an error."""


@dataclass
class OffProduct:
    barcode: str
    name: str
    brand: str
    quantity: str
    ingredients_text: str
    allergen_codes: list[str] = field(default_factory=list)
    trace_codes: list[str] = field(default_factory=list)
    url: str = ""
    image_url: str = ""
    updated_at: datetime | None = None  # when the database entry was last modified

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.brand, self.name, self.quantity) if p).strip()


def _codes(tags: list[str] | None) -> list[str]:
    return sorted({OFF_ALLERGENS[t] for t in tags or [] if t in OFF_ALLERGENS})


def _product(raw: dict, base_url: str) -> OffProduct:
    code = str(raw.get("code", ""))
    return OffProduct(
        barcode=code,
        name=(raw.get("product_name_en") or raw.get("product_name") or "").strip(),
        brand=(raw.get("brands") or "").split(",")[0].strip(),
        quantity=(raw.get("quantity") or "").strip(),
        ingredients_text=(raw.get("ingredients_text_en") or raw.get("ingredients_text") or "").strip(),
        allergen_codes=_codes(raw.get("allergens_tags")),
        trace_codes=_codes(raw.get("traces_tags")),
        url=f"{base_url}/product/{code}" if code else "",
        image_url=raw.get("image_front_url") or "",
        updated_at=_timestamp(raw.get("last_modified_t")),
    )


def _timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC) if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class OpenFoodFactsClient:
    def __init__(self, http: httpx.Client | None = None):
        s = get_settings()
        self.base_url = s.off_base_url.rstrip("/")
        self.http = http or httpx.Client(
            timeout=s.off_timeout_seconds, headers={"User-Agent": s.off_user_agent}
        )

    def _get(self, path: str, params: dict) -> dict:
        try:
            r = self.http.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as e:
            raise OffUnavailable(str(e)) from e
        if r.status_code == 404:
            return {}
        if r.status_code >= 400:
            raise OffUnavailable(f"HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise OffUnavailable("Invalid JSON") from e

    def get_product(self, barcode: str) -> OffProduct | None:
        data = self._get(f"/api/v2/product/{barcode}.json", {"fields": FIELDS})
        if data.get("status") != 1 or not data.get("product"):
            return None
        raw = dict(data["product"])
        raw.setdefault("code", barcode)
        return _product(raw, self.base_url)

    def search(self, query: str, limit: int = 5) -> list[OffProduct]:
        data = self._get(
            "/cgi/search.pl",
            {
                "search_terms": query,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": limit,
                "fields": FIELDS,
            },
        )
        return [_product(p, self.base_url) for p in data.get("products", []) if p.get("code")]
