"""Product snapshots: keep each distinct version of a product's database record to spot formulation changes."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ProductSnapshot
from app.models.common import utcnow
from app.packaged.off import OffProduct

SOURCE = "open_food_facts"


def content_hash(p: OffProduct) -> str:
    body = json.dumps(
        [" ".join(p.ingredients_text.lower().split()), sorted(p.allergen_codes), sorted(p.trace_codes)],
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _items(text: str) -> set[str]:
    return {" ".join(t.split()) for t in text.lower().replace(";", ",").split(",") if t.strip()}


def record_snapshot(db: Session, p: OffProduct) -> tuple[ProductSnapshot, list[str]]:
    """Store the record if its content is new. Returns the snapshot and a description of changes
    compared with the previous snapshot of the same barcode (empty if unchanged or first seen)."""
    h = content_hash(p)
    now = utcnow()
    existing = db.scalar(select(ProductSnapshot).where(
        ProductSnapshot.barcode == p.barcode, ProductSnapshot.source == SOURCE, ProductSnapshot.content_hash == h))
    if existing is not None:
        existing.last_seen = now
        db.flush()
        return existing, []
    previous = db.scalar(
        select(ProductSnapshot)
        .where(ProductSnapshot.barcode == p.barcode, ProductSnapshot.source == SOURCE)
        .order_by(ProductSnapshot.last_seen.desc(), ProductSnapshot.id.desc())
        .limit(1)
    )
    snap = ProductSnapshot(
        barcode=p.barcode, source=SOURCE, content_hash=h, name=p.display_name, ingredients_text=p.ingredients_text,
        allergen_codes=sorted(p.allergen_codes), trace_codes=sorted(p.trace_codes), source_updated_at=p.updated_at,
        first_seen=now, last_seen=now,
    )
    db.add(snap)
    db.flush()
    if previous is None:
        return snap, []
    changes: list[str] = []
    old, new = _items(previous.ingredients_text), _items(p.ingredients_text)
    if new - old:
        changes.append("ingredients added: " + ", ".join(sorted(new - old)))
    if old - new:
        changes.append("ingredients removed: " + ", ".join(sorted(old - new)))
    oa, na = set(previous.allergen_codes), set(p.allergen_codes)
    if na - oa:
        changes.append("allergens added: " + ", ".join(sorted(na - oa)))
    if oa - na:
        changes.append("allergens removed: " + ", ".join(sorted(oa - na)))
    ot, nt = set(previous.trace_codes), set(p.trace_codes)
    if nt != ot:
        changes.append("trace warnings changed")
    return snap, changes or ["the record changed"]


def saved_copy(db: Session, barcode: str) -> tuple[OffProduct, ProductSnapshot] | None:
    """The most recently seen record for a barcode, used when the product database is unreachable."""
    snap = db.scalar(
        select(ProductSnapshot)
        .where(ProductSnapshot.barcode == barcode, ProductSnapshot.source == SOURCE)
        .order_by(ProductSnapshot.last_seen.desc(), ProductSnapshot.id.desc())
        .limit(1)
    )
    if snap is None:
        return None
    p = OffProduct(
        barcode=snap.barcode, name=snap.name, brand="", quantity="", ingredients_text=snap.ingredients_text,
        allergen_codes=list(snap.allergen_codes), trace_codes=list(snap.trace_codes),
        url=f"{get_settings().off_base_url.rstrip('/')}/product/{snap.barcode}", updated_at=snap.source_updated_at,
    )
    return p, snap


def record_page(db: Session, product, src) -> None:
    """Keep each distinct external page version used as evidence (its ingredient text and where it came from)."""
    from urllib.parse import urlparse

    source = f"{'manufacturer' if src.type.value == 'manufacturer' else 'web'}:{urlparse(src.url or '').netloc}"[:40]
    h = hashlib.sha256(json.dumps([src.url, src.ingredients_text]).encode()).hexdigest()
    key = product.barcode or product.label.lower()[:20]
    existing = db.scalar(select(ProductSnapshot).where(ProductSnapshot.barcode == key, ProductSnapshot.source == source,
                                                       ProductSnapshot.content_hash == h))
    now = utcnow()
    if existing:
        existing.last_seen = now
    else:
        db.add(ProductSnapshot(barcode=key, source=source, content_hash=h, name=src.name[:200],
                               ingredients_text=src.ingredients_text or "", allergen_codes=[], trace_codes=[],
                               source_updated_at=src.as_of, first_seen=now, last_seen=now))
    db.flush()
