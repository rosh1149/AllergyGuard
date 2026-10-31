"""Stored evidence and assessments, so every result can be traced and re-checked later."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
