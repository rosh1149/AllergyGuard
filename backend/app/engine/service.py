"""Glue between the engine and the database: knowledge base loading and the user's profile."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.engine.assess import assess
from app.engine.explain import add_explanation
from app.engine.knowledge import AllergenInfo, ConceptInfo, KnowledgeBase
from app.core.background import submit
from app.core.observability import log_event
from app.engine.types import AllergyProfile, AnalysisContext, AnalysisResult, EvidenceSource, StageRecord
from app.models import (
    ACTIVE_STATUSES,
    Assessment,
    Allergen,
    AllergenParent,
    AllergenTermKind,
    ConceptAlias,
    ConceptRelation,
    IngredientConcept,
    KnowledgeStatus,
    KnowledgeVersion,
    RegulatoryList,
    UnresolvedIngredient,
    User,
)
from app.models.common import utcnow
from app.seed.concept_data import HOMONYMS, MODIFIERS

_kb: KnowledgeBase | None = None
_lock = threading.Lock()


def knowledge_version(db: Session) -> int:
    return db.scalar(select(func.max(KnowledgeVersion.version))) or 0


def load_knowledge_base(db: Session) -> KnowledgeBase:
    allergens = db.scalars(select(Allergen).options(selectinload(Allergen.terms))).all()
    synonyms, exclusions = [], []
    for a in allergens:
        for t in a.terms:
            (synonyms if t.kind == AllergenTermKind.synonym else exclusions).append((t.term, a.code))

    concepts = db.scalars(select(IngredientConcept).where(IngredientConcept.status.in_(ACTIVE_STATUSES))).all()
    by_id = {c.id: c for c in concepts}
    aliases = db.scalars(select(ConceptAlias).where(ConceptAlias.status.in_(ACTIVE_STATUSES))).all()
    codes = {a.id: a.code for a in allergens}
    relations = []
    for r in db.scalars(select(ConceptRelation).where(ConceptRelation.status.in_(ACTIVE_STATUSES))).all():
        if r.subject_id not in by_id:
            continue
        if r.object_allergen_id is not None:
            obj = f"allergen:{codes[r.object_allergen_id]}"
        elif r.object_concept_id in by_id:
            obj = by_id[r.object_concept_id].slug
        else:
            continue
        # Knowledge not yet verified may raise a possibility, never establish a definite link.
        cert = "possible" if r.status == KnowledgeStatus.unverified else r.certainty.value
        relations.append((by_id[r.subject_id].slug, r.relation.value, obj, cert))

    hierarchy = [
        (codes[h.child_id], codes[h.parent_id])
        for h in db.scalars(select(AllergenParent).where(AllergenParent.status.in_(ACTIVE_STATUSES))).all()
    ]
    regulated: dict[str, list[str]] = {}
    for rl in db.scalars(select(RegulatoryList).where(RegulatoryList.status.in_(ACTIVE_STATUSES))).all():
        for e in rl.entries:
            regulated.setdefault(codes[e.allergen_id], []).append(rl.jurisdiction)

    return KnowledgeBase(
        [AllergenInfo(a.code, a.name) for a in allergens],
        synonyms,
        exclusions,
        concepts=[ConceptInfo(c.slug, c.name, c.ambiguous_source, c.foodon_id, c.concept_type) for c in concepts],
        aliases=[(a.alias, by_id[a.concept_id].slug) for a in aliases if a.concept_id in by_id],
        relations=relations,
        hierarchy=hierarchy,
        regulated={k: sorted(set(v)) for k, v in regulated.items()},
        modifiers=MODIFIERS,
        version=knowledge_version(db),
        homonyms=HOMONYMS,
    )


def get_knowledge_base(db: Session) -> KnowledgeBase:
    """Cached per process and reloaded when the knowledge version changes (an approved change)."""
    global _kb
    current = knowledge_version(db)
    with _lock:
        if _kb is None or _kb.version != current:
            _kb = load_knowledge_base(db)
        return _kb


def reset_knowledge_base_cache() -> None:
    global _kb
    with _lock:
        _kb = None


def profile_for(user: User) -> AllergyProfile:
    return AllergyProfile(
        allergen_codes={ca.allergen.code for ca in user.allergies},
        custom_terms=[c.term for c in user.custom_allergies],
    )


def record_unresolved(db: Session, result: AnalysisResult) -> None:
    """Queue ingredient text the knowledge base did not recognise, for manual review.

    Only the text and the kind of source are kept: no user, restaurant or product identifiers."""
    source_types = {s.id: s.type.value for s in result.sources}
    seen: dict[str, set[str]] = {}
    detail: dict[str, tuple[list[str], list[str]]] = {}
    for ing in result.ingredients:
        if ing.status == "unresolved" and 2 <= len(ing.text) <= 200:
            seen.setdefault(ing.text, set()).add(source_types.get(ing.source_id, ""))
            detail[ing.text] = (ing.candidates, ing.unresolved_reasons)
    if not seen:
        return
    existing = {u.text: u for u in db.scalars(select(UnresolvedIngredient).where(UnresolvedIngredient.text.in_(seen))).all()}
    now = utcnow()
    for text, types in seen.items():
        u = existing.get(text)
        candidates, reasons = detail.get(text, ([], []))
        if u is None:
            db.add(UnresolvedIngredient(text=text, occurrences=1, source_types=sorted(types), first_seen=now, last_seen=now,
                                        candidates=candidates, reasons=reasons))
        else:
            u.occurrences += 1
            u.last_seen = now
            u.source_types = sorted(set(u.source_types) | types)
            u.candidates = list(dict.fromkeys([*(u.candidates or []), *candidates]))
            u.reasons = reasons or u.reasons
    db.commit()


@dataclass
class AssessmentLink:
    """What an assessment was about, so it can be found again and checked for staleness."""

    kind: str  # packaged | restaurant | text
    barcode: str | None = None
    product_snapshot_id: int | None = None
    restaurant_id: int | None = None
    menu_item_id: int | None = None
    recipe_version_id: int | None = None


def _source_record(s) -> dict:
    return {
        "id": s.id, "type": s.type.value, "name": s.name, "reference": s.reference, "verification": s.verification,
        "as_of": s.as_of.isoformat() if s.as_of else None, "fresh": s.fresh, "completeness": s.completeness,
        "used_for_absence": s.used_for_absence, "ingredients": s.ingredients,
    }


def store_assessment(db: Session, user: User, result: AnalysisResult, link: AssessmentLink) -> Assessment:
    """Keep the outcome per allergen, the exact sources used and the knowledge version (no photos)."""
    a = Assessment(
        customer_id=user.id,
        kind=link.kind,
        label=result.identity_label,
        overall_outcome=result.overall_outcome.value,
        outcomes={x.code: x.outcome.value for x in result.your_allergens + result.other_allergens},
        sources=[_source_record(s) for s in result.sources],
        knowledge_version=result.knowledge_version,
        barcode=link.barcode,
        product_snapshot_id=link.product_snapshot_id,
        restaurant_id=link.restaurant_id,
        menu_item_id=link.menu_item_id,
        recipe_version_id=link.recipe_version_id,
        result={},
        trace=[st.model_dump() for st in result.trace],
        duration_ms=result.duration_ms,
        created_at=result.evaluated_at or utcnow(),
    )
    db.add(a)
    db.flush()
    result.assessment_id = a.id
    a.result = result.model_dump(mode="json")
    db.commit()
    return a


def analyze_for_user(
    db: Session,
    user: User,
    sources: list[EvidenceSource],
    ctx: AnalysisContext | None = None,
    use_llm: bool = True,
    record: AssessmentLink | None = None,
) -> AnalysisResult:
    """Entry point for pipelines: the user's saved allergy profile is always applied.

    With `record`, the assessment is stored in the customer's history (result.assessment_id)."""
    ctx = ctx or AnalysisContext()
    started = ctx.started if ctx.started is not None else time.perf_counter()
    result = assess(get_knowledge_base(db), sources, profile_for(user), ctx)
    t = time.perf_counter()
    result = add_explanation(result, use_llm=use_llm)
    result.trace.append(StageRecord(name="explanation", duration_ms=round((time.perf_counter() - t) * 1000),
                                    counts={"source": result.explanation_source}))
    result.duration_ms = round((time.perf_counter() - started) * 1000)
    if record is not None:
        store_assessment(db, user, result, record)
    # Queue unrecognised ingredient text for review after the response (it doesn't change this answer).
    submit("record_unresolved", lambda s: record_unresolved(s, result))
    _log_analysis(result, record)
    return result


def _log_analysis(result: AnalysisResult, record: AssessmentLink | None) -> None:
    log_event(
        "analysis.completed",
        kind=record.kind if record else "unrecorded",
        assessment_id=result.assessment_id,
        duration_ms=result.duration_ms,
        overall_outcome=result.overall_outcome.value,
        sources=len(result.sources),
        sources_used_for_absence=sum(s.used_for_absence for s in result.sources),
        conflicts=len(result.conflicts),
        unresolved_items=sum(1 for i in result.ingredients if i.status == "unresolved"),
        failed_stages=[st.name for st in result.trace if st.status == "failed"],
        knowledge_version=result.knowledge_version,
    )
