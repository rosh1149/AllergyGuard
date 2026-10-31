"""Review queue: uncertain input kept for a person to look at, instead of being forced into a conclusion."""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import utcnow


class ReviewKind(str, enum.Enum):
    low_confidence_product = "low_confidence_product"  # product identified, but not confidently
    multiple_product_matches = "multiple_product_matches"  # several products matched about equally
    unreadable_label = "unreadable_label"  # photos given, but the ingredient list could not be read reliably
    incomplete_label = "incomplete_label"  # text read, but no "Ingredients" heading: may be partial
    low_confidence_dish = "low_confidence_dish"  # a menu item matched, but not confidently
    multiple_dish_matches = "multiple_dish_matches"  # several menu items matched about equally
    unmatched_dish = "unmatched_dish"  # customers asked about a dish the menu does not have


# Kinds that belong to one restaurant and are shown to its staff.
RESTAURANT_KINDS = {ReviewKind.low_confidence_dish, ReviewKind.multiple_dish_matches, ReviewKind.unmatched_dish}


class ReviewStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


class ReviewItem(Base):
    """One open question, merged across repeat occurrences (same kind, restaurant and key).

    Holds the text that was uncertain and the scores involved. No photos and no customer identity."""

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ReviewStatus.open.value, index=True)
    restaurant_id: Mapped[int | None] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_assessment_id: Mapped[int | None] = mapped_column(ForeignKey("assessments.id", ondelete="SET NULL"))
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
