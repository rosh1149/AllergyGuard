"""Manufacturer pages and web search (Stage 3).

Rules that keep external pages from being over-trusted:
- A page counts as MANUFACTURER evidence only on a domain an administrator approved for that brand.
  Any other page is a WEB PAGE: a candidate that can raise a warning but never confirm or rule out.
- Before a page is used, the product on it must match the product being checked: the barcode appears,
  or the brand appears and the page title is a close match for the product name with the same variant.
- Page text is data. It is parsed with fixed rules (no model reads it), so text such as "ignore
  previous instructions" can only ever be an unrecognised ingredient.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.similarity import name_similarity
from app.engine.types import EvidenceSource, SourceType
from app.models.common import utcnow
from app.packaged.ocr import extract_ingredient_section
from app.sources.base import AdapterError, AdapterResult, AdapterStatus, SourceAdapter, register

# Words that name a product variant: a page for "... Dark" is not the page for "... Milk".
VARIANT_WORDS = {
    "original", "classic", "dark", "milk", "white", "plain", "salted", "unsalted", "sweet", "spicy", "hot", "mild",
    "light", "lite", "diet", "zero", "sugar-free", "gluten-free", "vegan", "organic", "chocolate", "vanilla",
    "strawberry", "orange", "lemon", "honey", "nut", "nuts", "peanut", "almond", "hazelnut", "coconut", "cheese",
    "cheddar", "sour", "cream", "onion", "garlic", "chilli", "chili", "masala", "mint", "caramel", "toffee",
}


@dataclass
class ProductIdentity:
    brand: str = ""
    name: str = ""
    barcode: str = ""

    @property
    def label(self) -> str:
        return " ".join(p for p in (self.brand, self.name) if p).strip()


@dataclass
class Page:
    url: str
    title: str
    text: str
    last_modified: datetime | None = None
    content_hash: str = ""


@dataclass
class _TextParser(HTMLParser):
    parts: list[str] = field(default_factory=list)
    title: list[str] = field(default_factory=list)
    _skip: int = 0
    _in_title: bool = False

    def __post_init__(self):
        super().__init__(convert_charrefs=True)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "dd", "dt"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        (self.title if self._in_title else self.parts).append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextParser()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return " ".join("".join(p.title).split()), text


def fetch_page(client: httpx.Client, url: str) -> Page:
    s = get_settings()
    if urlparse(url).scheme not in ("http", "https"):
        raise AdapterError(AdapterStatus.malformed, "Only http(s) pages are fetched.")
    try:
        r = client.get(url, follow_redirects=True, timeout=s.web_timeout_seconds)
    except httpx.HTTPError as e:
        raise AdapterError(AdapterStatus.unavailable, f"{type(e).__name__}") from e
    if r.status_code == 429:
        raise AdapterError(AdapterStatus.rate_limited, "HTTP 429")
    if r.status_code == 404:
        raise AdapterError(AdapterStatus.not_found, "HTTP 404")
    if r.status_code >= 400:
        raise AdapterError(AdapterStatus.unavailable, f"HTTP {r.status_code}")
    if "html" not in r.headers.get("content-type", "text/html"):
        raise AdapterError(AdapterStatus.malformed, "Not an HTML page.")
    body = r.content[: s.web_page_max_bytes].decode(r.encoding or "utf-8", errors="replace")
    title, text = html_to_text(body)
    lm = None
    if r.headers.get("last-modified"):
        try:
            lm = parsedate_to_datetime(r.headers["last-modified"])
        except (TypeError, ValueError):
            lm = None
    return Page(str(r.url), title, text, lm, hashlib.sha256(text.encode()).hexdigest())


def _variants(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]+", text.lower()) if w in VARIANT_WORDS}


def identity_check(page: Page, product: ProductIdentity) -> tuple[bool, str]:
    """Is this page about the product being checked? Returns (ok, reason)."""
    digits = re.sub(r"\D", "", page.text + " " + page.url)
    if product.barcode and product.barcode in digits:
        return True, f"barcode {product.barcode} appears on the page"
    if not product.brand or product.brand.lower() not in (page.title + " " + page.text[:3000]).lower():
        return False, f"the brand '{product.brand or '?'}' does not appear on the page"
    sim = max(name_similarity(product.label, page.title), name_similarity(product.name, page.title))
    if sim < 0.7:
        return False, f"the page title '{page.title[:80]}' does not match '{product.label}' ({sim:.0%})"
    want, have = _variants(product.label), _variants(page.title)
    if have - want or want - have:
        return False, (f"the variant differs (product: {', '.join(sorted(want)) or 'none'}; "
                       f"page: {', '.join(sorted(have)) or 'none'})")
    return True, f"brand and product name match the page title ({sim:.0%})"


def page_source(page: Page, product: ProductIdentity, manufacturer: bool, n: int) -> EvidenceSource | None:
    section = extract_ingredient_section(page.text)
    if section is None or not section.found_heading:
        return None
    domain = urlparse(page.url).netloc
    return EvidenceSource(
        id=f"{'mfr' if manufacturer else 'web'}:{n}",
        type=SourceType.manufacturer if manufacturer else SourceType.web_page,
        name=f"{'Manufacturer site' if manufacturer else 'Web page'} ({domain}): {page.title[:80] or product.label}",
        ingredients_text=section.ingredients_text,
        complete=True,
        url=page.url,
        reference=page.url,
        verification="manufacturer site (approved domain)" if manufacturer else "web page (not verified)",
        as_of=page.last_modified or utcnow(),
        note=f"Page text hash {page.content_hash[:12]}",
    )


def domain_approved(url: str, sites: list[tuple[str, str]], brand: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(b == brand.lower() and (host == d or host.endswith("." + d)) for b, d in sites)


@register
class ManufacturerPageAdapter(SourceAdapter):
    name = "manufacturer_pages"
    remote = True
    source_type = SourceType.manufacturer
    description = "The product's page on its manufacturer's website (approved domains only)."

    def __init__(self, client: httpx.Client | None, sites: list[tuple[str, str, str]]):
        self.client, self.sites = client, sites  # (brand, domain, url template)

    def fetch(self, product: ProductIdentity) -> AdapterResult:
        def go():
            if self.client is None or not get_settings().manufacturer_pages_enabled:
                return AdapterStatus.skipped, [], None, "Manufacturer pages are not enabled."
            urls = [t.format(barcode=product.barcode) for b, _, t in self.sites
                    if b == product.brand.lower() and t and ("{barcode}" not in t or product.barcode)]
            if not urls:
                return AdapterStatus.skipped, [], None, f"No approved manufacturer page for '{product.brand}'."
            page = fetch_page(self.client, urls[0])
            ok, why = identity_check(page, product)
            if not ok:
                return AdapterStatus.not_found, [], {"rejected": [(page.url, why)]}, f"Page rejected: {why}."
            src = page_source(page, product, True, 1)
            if src is None:
                return AdapterStatus.not_found, [], {"page": page}, "No ingredient list on the manufacturer page."
            return AdapterStatus.ok, [src], {"page": page, "identity": why}, f"Manufacturer page: {page.url}"
        return self.run(go)


class SearchProvider:
    """Returns [(url, title)] for a query. Implementations need an API key."""

    def search(self, client: httpx.Client, query: str) -> list[tuple[str, str]]:
        raise NotImplementedError


class BraveSearch(SearchProvider):
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, key: str):
        self.key = key

    def search(self, client, query):
        try:
            r = client.get(self.URL, params={"q": query, "count": 5},
                           headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                           timeout=get_settings().web_timeout_seconds)
        except httpx.HTTPError as e:
            raise AdapterError(AdapterStatus.unavailable, type(e).__name__) from e
        if r.status_code == 429:
            raise AdapterError(AdapterStatus.rate_limited, "HTTP 429")
        if r.status_code >= 400:
            raise AdapterError(AdapterStatus.unavailable, f"HTTP {r.status_code}")
        try:
            results = r.json()["web"]["results"]
            return [(x["url"], x.get("title", "")) for x in results if x.get("url")]
        except (ValueError, KeyError, TypeError) as e:
            raise AdapterError(AdapterStatus.malformed, "Unexpected search response") from e


def search_provider() -> SearchProvider | None:
    s = get_settings()
    if s.web_search_provider == "brave" and s.web_search_api_key:
        return BraveSearch(s.web_search_api_key)
    return None


@register
class WebSearchAdapter(SourceAdapter):
    name = "web_search"
    remote = True
    source_type = SourceType.web_page
    description = "Web search for the product's ingredient list; approved manufacturer domains count as manufacturer evidence, other pages as unverified candidates."

    def __init__(self, client: httpx.Client | None, provider: SearchProvider | None, sites: list[tuple[str, str, str]],
                 max_pages: int = 3):
        self.client, self.provider, self.max_pages = client, provider, max_pages
        self.sites = [(b, d) for b, d, _ in sites]

    def fetch(self, product: ProductIdentity) -> AdapterResult:
        def go():
            if self.client is None or self.provider is None:
                return AdapterStatus.skipped, [], None, "Web search is not configured."
            query = f"{product.label} ingredients"
            results = self.provider.search(self.client, query)
            sources, rejected = [], []
            for url, _ in results[: self.max_pages]:
                try:
                    page = fetch_page(self.client, url)
                except AdapterError as e:
                    rejected.append((url, f"could not be retrieved ({e.status.value})"))
                    continue
                ok, why = identity_check(page, product)
                if not ok:
                    rejected.append((url, why))
                    continue
                src = page_source(page, product, domain_approved(url, self.sites, product.brand), len(sources) + 1)
                if src is None:
                    rejected.append((url, "no ingredient list found"))
                    continue
                sources.append(src)
            status = AdapterStatus.ok if sources else AdapterStatus.not_found
            return status, sources, {"rejected": rejected}, f"{len(sources)} page(s) used, {len(rejected)} rejected."
        return self.run(go)
