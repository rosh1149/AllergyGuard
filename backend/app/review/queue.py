"""Adding uncertain input to the review queue and resolving it."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ReviewItem, ReviewKind, ReviewStatus, User
from app.models.common import utcnow


@dataclass
class ReviewFlag:
    kind: ReviewKind
    key: str
    summary: str
    details: dict = field(default_factory=dict)
    restaurant_id: int | None = None


def _key(text: str) -> str:
    return " ".join(text.lower().split())[:300] or "(none)"


def queue_flags(db: Session, flags: list[ReviewFlag], assessment_id: int | None) -> list[ReviewItem]:
    """Merge each flag into the open item with the same kind, restaurant and key, or open a new one."""
    items = []
    now = utcnow()
    for f in flags:
        key = _key(f.key)
        item = db.scalar(select(ReviewItem).where(
            ReviewItem.kind == f.kind.value, ReviewItem.key == key, ReviewItem.status == ReviewStatus.open.value,
            ReviewItem.restaurant_id.is_(None) if f.restaurant_id is None else ReviewItem.restaurant_id == f.restaurant_id,
        ))
        if item is None:
            item = ReviewItem(kind=f.kind.value, key=key, restaurant_id=f.restaurant_id, occurrences=0, first_seen=now)
            db.add(item)
        item.occurrences = (item.occurrences or 0) + 1
        item.summary = f.summary
        item.details = f.details
        item.last_seen = now
        item.last_assessment_id = assessment_id
        items.append(item)
    if items:
        db.commit()
    return items


def resolve(db: Session, item: ReviewItem, actor: User, new_status: ReviewStatus, note: str) -> ReviewItem:
    if item.status != ReviewStatus.open.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This item was already {item.status}.")
    if new_status == ReviewStatus.open:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Status must be 'resolved' or 'dismissed'.")
    item.status = new_status.value
    item.resolution_note = note
    item.resolved_by_id = actor.id
    item.resolved_at = utcnow()
    db.flush()
    return item
