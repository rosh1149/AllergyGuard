"""Platform monitoring for administrators: aggregate metrics and the admin audit log.

Everything here is aggregated or technical: no customer identities, allergy profiles or photos."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Annotated

import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DB, CurrentAdmin
from app.core.background import stats as background_stats
from app.core.limits import image_job_stats, process_stats
from app.engine.normalize import normalization_cache_stats
from app.engine.service import knowledge_version
from app.engine.types import MIN_IDENTITY_CONFIDENCE
from app.models import Assessment, AuditRecord, ManufacturerSite, ReviewItem, ReviewStatus, UnresolvedIngredient, UnresolvedStatus
from app.models.common import utcnow
from app.packaged.off import OFF_CACHE
from app.services.admin_audit import admin_audit
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
        "process": process_stats(),
    }


# ---- assessment trace lookup ----
# For debugging a slow or failed check. Shows only technical data: kind, timing, processing stages,
# knowledge version, outcome counts and the types of source used. Never the customer, what they
# checked, the ingredients, or which allergens were checked (that would reveal their allergy profile).


class TraceSummaryOut(BaseModel):
    id: int
    kind: str
    created_at: datetime
    duration_ms: int | None
    knowledge_version: int
    stages: int
    failed_stages: list[str]


class TraceOut(TraceSummaryOut):
    outcome_counts: dict[str, int]
    source_types: list[dict]
    trace: list[dict]


def _summary(a: Assessment) -> dict:
    trace = a.trace or []
    return {"id": a.id, "kind": a.kind, "created_at": a.created_at, "duration_ms": a.duration_ms,
            "knowledge_version": a.knowledge_version, "stages": len(trace),
            "failed_stages": [st.get("name", "") for st in trace if st.get("status") == "failed"]}


@router.get("/assessments", response_model=list[TraceSummaryOut])
def list_traces(
    _: CurrentAdmin, db: DB,
    kind: Annotated[str | None, Query(pattern="^(packaged|restaurant|text)$")] = None,
    min_duration_ms: Annotated[int | None, Query(ge=0)] = None,
    failed_only: bool = False,
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
    sort: Annotated[str, Query(pattern="^(recent|slowest)$")] = "recent",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    """Recent assessments for debugging: filter by kind, minimum duration or failed stages."""
    q = select(Assessment).where(Assessment.created_at >= utcnow() - timedelta(hours=hours))
    if kind:
        q = q.where(Assessment.kind == kind)
    if min_duration_ms is not None:
        q = q.where(Assessment.duration_ms >= min_duration_ms)
    q = q.order_by(Assessment.duration_ms.desc().nulls_last() if sort == "slowest" else Assessment.created_at.desc())
    rows = [_summary(a) for a in db.scalars(q.limit(limit * 5 if failed_only else limit))]
    if failed_only:
        rows = [r for r in rows if r["failed_stages"]][:limit]
    return rows


@router.get("/assessments/{assessment_id}", response_model=TraceOut)
def get_trace(assessment_id: int, admin: CurrentAdmin, db: DB) -> dict:
    a = db.get(Assessment, assessment_id)
    if a is None:
        raise HTTPException(404, "Assessment not found.")
    admin_audit(db, admin, "assessment.trace_viewed", "assessment", a.id, {})
    return {**_summary(a),
            "outcome_counts": dict(Counter((a.outcomes or {}).values())),
            "source_types": [{"type": s.get("type"), "verification": s.get("verification"),
                              "fresh": s.get("fresh"), "completeness": s.get("completeness"),
                              "used_for_absence": s.get("used_for_absence")} for s in a.sources or []],
            "trace": a.trace or []}


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


# ---- approved manufacturer sites (Stage 3) ----


class ManufacturerSiteIn(BaseModel):
    brand: str
    domain: str
    product_url_template: str = ""
    note: str = ""


def _site_out(m: ManufacturerSite) -> dict:
    return {"id": m.id, "brand": m.brand, "domain": m.domain, "product_url_template": m.product_url_template,
            "note": m.note, "created_at": m.created_at}


@router.get("/manufacturer-sites")
def list_sites(_: CurrentAdmin, db: DB) -> list[dict]:
    return [_site_out(m) for m in db.scalars(select(ManufacturerSite).order_by(ManufacturerSite.brand)).all()]


@router.post("/manufacturer-sites", status_code=201)
def add_site(data: ManufacturerSiteIn, admin: CurrentAdmin, db: DB) -> dict:
    brand = " ".join(data.brand.lower().split())
    domain = data.domain.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
    if not brand or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise HTTPException(422, "Give the brand and a domain such as 'acmebakery.example'.")
    tpl = data.product_url_template.strip()
    if tpl and urlparse(tpl).netloc.lower().removeprefix("www.") not in (domain,) and not urlparse(tpl).netloc.lower().endswith("." + domain):
        raise HTTPException(422, "The product page pattern must be on the approved domain.")
    if db.scalar(select(ManufacturerSite.id).where(ManufacturerSite.brand == brand, ManufacturerSite.domain == domain)):
        raise HTTPException(409, "That site is already approved for this brand.")
    m = ManufacturerSite(brand=brand, domain=domain, product_url_template=tpl, note=data.note, added_by_id=admin.id)
    db.add(m)
    db.commit()
    admin_audit(db, admin, "manufacturer_site.approved", "manufacturer_site", m.id, {"brand": brand, "domain": domain})
    return _site_out(m)


@router.delete("/manufacturer-sites/{site_id}", status_code=204)
def remove_site(site_id: int, admin: CurrentAdmin, db: DB) -> None:
    m = db.get(ManufacturerSite, site_id)
    if m is None:
        raise HTTPException(404, "Site not found.")
    details = {"brand": m.brand, "domain": m.domain}
    db.delete(m)
    db.commit()
    admin_audit(db, admin, "manufacturer_site.removed", "manufacturer_site", site_id, details)
