"""Stored evidence and assessments, so every result can be traced and re-checked later."""

from datetime import datetime

from sqlalchemy import Index, JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import utcnow


class ProductSnapshot(Base):
    """One row per distinct version of a product's external record (e.g. an Open Food Facts entry).

    A new row is added only when the ingredient/allergen content changes, so formulation changes over
    time can be detected and shown."""

    __tablename__ = "product_snapshots"
    __table_args__ = (UniqueConstraint("barcode", "source", "content_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(40))  # "open_food_facts"
    content_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text, default="")
    ingredients_text: Mapped[str] = mapped_column(Text, default="")
    allergen_codes: Mapped[list] = mapped_column(JSON, default=list)
    trace_codes: Mapped[list] = mapped_column(JSON, default=list)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assessment(Base):
    """A stored analysis: outcome per allergen, the exact evidence used and the knowledge version.

    Belongs to the customer who ran it (their history). Holds no photos."""

    __tablename__ = "assessments"
    # Administrators list recent checks of one kind (e.g. the slowest packaged-product checks).
    __table_args__ = (Index("ix_assessments_kind_created_at", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # packaged | restaurant | text
    label: Mapped[str] = mapped_column(Text, default="")  # what was checked, as shown to the customer
    overall_outcome: Mapped[str] = mapped_column(String(40))
    outcomes: Mapped[dict] = mapped_column(JSON)  # allergen key -> outcome
    sources: Mapped[list] = mapped_column(JSON)  # id, type, reference, verification, as_of, ingredients
    knowledge_version: Mapped[int] = mapped_column(Integer)
    barcode: Mapped[str | None] = mapped_column(String(20))
    product_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("product_snapshots.id", ondelete="SET NULL"))
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id", ondelete="SET NULL"))
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id", ondelete="SET NULL"))
    recipe_version_id: Mapped[int | None] = mapped_column(ForeignKey("recipe_versions.id", ondelete="SET NULL"))
    result: Mapped[dict] = mapped_column(JSON)  # the full result as returned
    trace: Mapped[list] = mapped_column(JSON, default=list)  # processing stages (no personal data)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ManufacturerSite(Base):
    """A manufacturer's own website, approved by an administrator. Only pages on an approved domain for
    the product's brand count as manufacturer evidence; any other page is an unverified web page."""

    __tablename__ = "manufacturer_sites"
    __table_args__ = (UniqueConstraint("brand", "domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand: Mapped[str] = mapped_column(String(200), index=True)  # lower-case brand name as on the product
    domain: Mapped[str] = mapped_column(String(200))  # e.g. "acmebakery.example"
    # Optional product page pattern, with {barcode} filled in: "https://acmebakery.example/p/{barcode}"
    product_url_template: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
