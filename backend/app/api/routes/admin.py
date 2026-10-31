"""Platform monitoring for administrators: aggregate metrics and the admin audit log.

Everything here is aggregated or technical: no customer identities, allergy profiles or photos."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, CurrentAdmin
from app.core.background import stats as background_stats
from app.core.limits import image_job_stats
from app.engine.normalize import normalization_cache_stats
from app.engine.service import knowledge_version
from app.engine.types import MIN_IDENTITY_CONFIDENCE
from app.models import Assessment, AuditRecord, ReviewItem, ReviewStatus, UnresolvedIngredient, UnresolvedStatus
from app.models.common import utcnow
from app.packaged.off import OFF_CACHE
from app.sources import adapter_health

router = APIRouter(prefix="/admin", tags=["monitoring (admin)"])


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


@router.get("/metrics")
def metrics(_: CurrentAdmin, db: DB, hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24) -> dict:
    since = utcnow() - timedelta(hours=hours)
    rows = db.execute(
        select(Assessment.kind, Assessment.overall_outcome, Assessment.duration_ms, Assessment.trace)
        .where(Assessment.created_at >= since)
    ).all()
    by_kind, outcomes = Counter(), Counter()
    latency: dict[str, list[int]] = defaultdict(list)
    stage_ms: dict[str, list[int]] = defaultdict(list)
    failed_stages: Counter = Counter()
    rules: Counter = Counter()
    with_conflicts = low_confidence = with_unresolved = with_failure = 0
    for kind, overall, duration, trace in rows:
        by_kind[kind] += 1
        outcomes[overall] += 1
        if duration is not None:
            latency[kind].append(duration)
        failed = False
        for st in trace or []:
            if st.get("duration_ms") is not None:
                stage_ms[st["name"]].append(st["duration_ms"])
            if st.get("status") == "failed":
                failed_stages[st["name"]] += 1
                failed = True
            c = st.get("counts") or {}
            if st["name"] == "fusion":
                with_conflicts += bool(c.get("conflicts"))
                ic = c.get("identity_confidence")
                low_confidence += ic is not None and ic < MIN_IDENTITY_CONFIDENCE
            elif st["name"] == "concept_mapping":
                with_unresolved += bool(c.get("status_unresolved"))
            elif st["name"] == "decision":
                rules.update({k.removeprefix("rule_"): v for k, v in c.items() if k.startswith("rule_")})
        with_failure += failed
    total = len(rows)

    def share(n: int) -> float | None:
        return round(n / total, 3) if total else None

    open_reviews = dict(db.execute(select(ReviewItem.kind, func.count())
                                   .where(ReviewItem.status == ReviewStatus.open.value).group_by(ReviewItem.kind)).all())
    return {
        "window_hours": hours,
        "assessments": {
            "total": total,
            "by_kind": dict(by_kind),
            "overall_outcomes": dict(outcomes),
            "latency_ms": {k: {"count": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "max": max(v)}
                           for k, v in latency.items()},
            "stage_latency_ms": {k: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95)} for k, v in sorted(stage_ms.items())},
            "with_conflicts": with_conflicts, "with_conflicts_share": share(with_conflicts),
            "low_confidence_identification": low_confidence, "low_confidence_share": share(low_confidence),
            "with_unresolved_ingredients": with_unresolved, "with_unresolved_share": share(with_unresolved),
            "with_failed_stage": with_failure, "failure_rate": share(with_failure),
            "failed_stages": dict(failed_stages),
            "decision_rules": dict(rules),
        },
        "review_queue_open": open_reviews,
        "unresolved_ingredients_open": db.scalar(select(func.count()).select_from(UnresolvedIngredient)
                                                 .where(UnresolvedIngredient.status == UnresolvedStatus.open)) or 0,
        "knowledge_version": knowledge_version(db),
        "sources": adapter_health(),
        "caches": {"normalization": normalization_cache_stats(), "open_food_facts": OFF_CACHE.stats()},
        "background_jobs": background_stats(),
        "image_jobs": image_job_stats(),
    }


class AdminAuditOut(BaseModel):
    id: int
    actor_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    details: dict
    created_at: datetime


@router.get("/audit", response_model=list[AdminAuditOut])
def admin_audit_log(_: CurrentAdmin, db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[AuditRecord]:
    """Platform-level actions (knowledge changes, review decisions). Restaurant actions are in each
    restaurant's own audit log."""
    return list(db.scalars(select(AuditRecord).where(AuditRecord.restaurant_id.is_(None))
                           .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc()).limit(limit)).all())
