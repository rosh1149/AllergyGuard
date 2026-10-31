"""The customer's stored assessments, with a check of whether each is still current."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, CurrentCustomer
from app.engine.service import knowledge_version
from app.models import Assessment, ProductSnapshot, RecipeVersion
from app.restaurant.recipes import current_version

router = APIRouter(prefix="/me/assessments", tags=["customer history"])


class AssessmentSummary(BaseModel):
    id: int
    kind: str
    label: str
    overall_outcome: str
    outcomes: dict[str, str]
    knowledge_version: int
    created_at: datetime
    barcode: str | None = None
    restaurant_id: int | None = None
    menu_item_id: int | None = None
    recipe_version_id: int | None = None
    # The evidence may have changed since: a newer recipe version, a changed product record or an
    # updated knowledge base. Run the check again for a current result.
    outdated: bool = False
    outdated_reasons: list[str] = []


class AssessmentDetail(AssessmentSummary):
    sources: list[dict]
    result: dict


def _outdated(db, a: Assessment, kv: int) -> list[str]:
    reasons = []
    if a.knowledge_version != kv:
        reasons.append(f"The allergen knowledge base has been updated (v{a.knowledge_version} -> v{kv}).")
    if a.recipe_version_id is not None:
        v = db.get(RecipeVersion, a.recipe_version_id)
        if v is None or current_version(v.menu_item) is not v:
            reasons.append("The restaurant has changed this recipe since.")
    elif a.menu_item_id is not None:
        reasons.append("The recipe version used is no longer available.")
    if a.barcode and a.product_snapshot_id is not None:
        latest = db.scalar(
            select(ProductSnapshot.id).where(ProductSnapshot.barcode == a.barcode)
            .order_by(ProductSnapshot.last_seen.desc(), ProductSnapshot.id.desc()).limit(1)
        )
        if latest is not None and latest != a.product_snapshot_id:
            reasons.append("The product's database record has changed since.")
    return reasons


def _summary(db, a: Assessment, kv: int, cls=AssessmentSummary, **extra):
    reasons = _outdated(db, a, kv)
    return cls(
        id=a.id, kind=a.kind, label=a.label, overall_outcome=a.overall_outcome, outcomes=a.outcomes,
        knowledge_version=a.knowledge_version, created_at=a.created_at, barcode=a.barcode,
        restaurant_id=a.restaurant_id, menu_item_id=a.menu_item_id, recipe_version_id=a.recipe_version_id,
        outdated=bool(reasons), outdated_reasons=reasons, **extra,
    )


@router.get("", response_model=list[AssessmentSummary])
def list_assessments(user: CurrentCustomer, db: DB, limit: int = Query(50, ge=1, le=200)) -> list[AssessmentSummary]:
    kv = knowledge_version(db)
    rows = db.scalars(
        select(Assessment).where(Assessment.customer_id == user.id)
        .order_by(Assessment.created_at.desc(), Assessment.id.desc()).limit(limit)
    ).all()
    return [_summary(db, a, kv) for a in rows]


@router.get("/count")
def count_assessments(user: CurrentCustomer, db: DB) -> dict:
    return {"count": db.scalar(select(func.count()).select_from(Assessment).where(Assessment.customer_id == user.id))}


@router.get("/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(assessment_id: int, user: CurrentCustomer, db: DB) -> AssessmentDetail:
    a = db.get(Assessment, assessment_id)
    if a is None or a.customer_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found.")
    return _summary(db, a, knowledge_version(db), AssessmentDetail, sources=a.sources, result=a.result)


@router.delete("/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assessment(assessment_id: int, user: CurrentCustomer, db: DB) -> None:
    a = db.get(Assessment, assessment_id)
    if a is None or a.customer_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found.")
    db.delete(a)
    db.commit()
