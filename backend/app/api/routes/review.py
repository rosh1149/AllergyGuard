"""Review queue for administrators (all items) and restaurant staff (their restaurant's items)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, EDITORS, ANY_MEMBER, CurrentAdmin
from app.models import ReviewItem, ReviewKind, ReviewStatus, RestaurantMembership, UnresolvedIngredient, UnresolvedStatus
from app.restaurant.recipes import audit
from app.review.queue import resolve
from app.services.admin_audit import admin_audit

admin_router = APIRouter(prefix="/review-queue", tags=["review queue (admin)"])
restaurant_router = APIRouter(prefix="/restaurants/{restaurant_id}/review-queue", tags=["restaurant dashboard"])


class ReviewItemOut(BaseModel):
    id: int
    kind: ReviewKind
    status: ReviewStatus
    restaurant_id: int | None
    key: str
    summary: str
    details: dict
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    resolution_note: str
    resolved_at: datetime | None


class ReviewDecisionIn(BaseModel):
    status: ReviewStatus  # resolved | dismissed
    note: str = Field(default="", max_length=2000)


class ReviewSummary(BaseModel):
    open_by_kind: dict[str, int]
    open_total: int
    open_unresolved_ingredients: int


def _out(i: ReviewItem) -> ReviewItemOut:
    return ReviewItemOut(
        id=i.id, kind=ReviewKind(i.kind), status=ReviewStatus(i.status), restaurant_id=i.restaurant_id, key=i.key,
        summary=i.summary, details=i.details or {}, occurrences=i.occurrences, first_seen=i.first_seen,
        last_seen=i.last_seen, resolution_note=i.resolution_note, resolved_at=i.resolved_at,
    )


def _list(db, q, item_status: ReviewStatus | None, kind: ReviewKind | None, limit: int) -> list[ReviewItemOut]:
    if item_status:
        q = q.where(ReviewItem.status == item_status.value)
    if kind:
        q = q.where(ReviewItem.kind == kind.value)
    q = q.order_by(ReviewItem.occurrences.desc(), ReviewItem.last_seen.desc()).limit(limit)
    return [_out(i) for i in db.scalars(q).all()]


@admin_router.get("", response_model=list[ReviewItemOut])
def admin_list(
    _: CurrentAdmin, db: DB,
    item_status: Annotated[ReviewStatus | None, Query(alias="status")] = ReviewStatus.open,
    kind: ReviewKind | None = None,
    restaurant_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ReviewItemOut]:
    q = select(ReviewItem)
    if restaurant_id is not None:
        q = q.where(ReviewItem.restaurant_id == restaurant_id)
    return _list(db, q, item_status, kind, limit)


@admin_router.get("/summary", response_model=ReviewSummary)
def admin_summary(_: CurrentAdmin, db: DB) -> ReviewSummary:
    rows = db.execute(
        select(ReviewItem.kind, func.count()).where(ReviewItem.status == ReviewStatus.open.value).group_by(ReviewItem.kind)
    ).all()
    unresolved = db.scalar(
        select(func.count()).select_from(UnresolvedIngredient).where(UnresolvedIngredient.status == UnresolvedStatus.open)
    )
    by_kind = {k: n for k, n in rows}
    return ReviewSummary(open_by_kind=by_kind, open_total=sum(by_kind.values()), open_unresolved_ingredients=unresolved or 0)


@admin_router.post("/{item_id}/decision", response_model=ReviewItemOut)
def admin_decide(item_id: int, data: ReviewDecisionIn, admin: CurrentAdmin, db: DB) -> ReviewItemOut:
    item = db.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found.")
    resolve(db, item, admin, data.status, data.note)
    db.commit()
    admin_audit(db, admin, f"review.{data.status.value}", "review_item", item.id,
                {"kind": item.kind, "restaurant_id": item.restaurant_id, "note": data.note})
    return _out(item)


Member = Annotated[RestaurantMembership, Depends(ANY_MEMBER)]
Editor = Annotated[RestaurantMembership, Depends(EDITORS)]


@restaurant_router.get("", response_model=list[ReviewItemOut])
def restaurant_list(
    m: Member, db: DB,
    item_status: Annotated[ReviewStatus | None, Query(alias="status")] = ReviewStatus.open,
    kind: ReviewKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ReviewItemOut]:
    """Uncertain dish matches and dishes customers asked for that are not on the menu."""
    return _list(db, select(ReviewItem).where(ReviewItem.restaurant_id == m.restaurant_id), item_status, kind, limit)


@restaurant_router.post("/{item_id}/decision", response_model=ReviewItemOut)
def restaurant_decide(item_id: int, data: ReviewDecisionIn, m: Editor, db: DB) -> ReviewItemOut:
    item = db.get(ReviewItem, item_id)
    if item is None or item.restaurant_id != m.restaurant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found.")
    resolve(db, item, m.user, data.status, data.note)
    audit(db, m.restaurant_id, m.user_id, f"review.{data.status.value}", "review_item", item.id,
          {"kind": item.kind, "key": item.key, "note": data.note})
    db.commit()
    return _out(item)


sources_router = APIRouter(prefix="/sources", tags=["source adapters (admin)"])


@sources_router.get("")
def list_sources(_: CurrentAdmin) -> list[dict]:
    """Registered source adapters and their health since the server started."""
    from app.sources import ADAPTERS, adapter_health
    import app.sources.label, app.sources.open_food_facts, app.sources.restaurant, app.sources.web  # noqa: E401,F401  (register all)

    health = adapter_health()
    return [
        {"name": name, "source_type": cls.source_type.value, "description": cls.description,
         "health": health.get(name, {"calls": 0, "failures": 0})}
        for name, cls in sorted(ADAPTERS.items())
    ]
