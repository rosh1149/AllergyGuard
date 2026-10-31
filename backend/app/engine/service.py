"""Glue between the engine and the database: knowledge base loading and the user's profile."""

from __future__ import annotations

import threading

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.engine.assess import assess
from app.engine.explain import add_explanation
from app.engine.knowledge import AllergenInfo, ConceptInfo, KnowledgeBase
from app.engine.types import AllergyProfile, AnalysisContext, AnalysisResult, EvidenceSource
from app.models import (
    ACTIVE_STATUSES,
    Allergen,
    AllergenParent,
    AllergenTermKind,
    ConceptAlias,
    ConceptRelation,
    IngredientConcept,
    KnowledgeVersion,
    RegulatoryList,
    UnresolvedIngredient,
    User,
)
from app.models.common import utcnow
from app.seed.concept_data import MODIFIERS

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
        relations.append((by_id[r.subject_id].slug, r.relation.value, obj, r.certainty.value))

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
        concepts=[ConceptInfo(c.slug, c.name, c.ambiguous_source, c.foodon_id) for c in concepts],
        aliases=[(a.alias, by_id[a.concept_id].slug) for a in aliases if a.concept_id in by_id],
        relations=relations,
        hierarchy=hierarchy,
        regulated={k: sorted(set(v)) for k, v in regulated.items()},
        modifiers=MODIFIERS,
        version=knowledge_version(db),
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
    for ing in result.ingredients:
        if ing.status == "unresolved" and 2 <= len(ing.text) <= 200:
            seen.setdefault(ing.text, set()).add(source_types.get(ing.source_id, ""))
    if not seen:
        return
    existing = {u.text: u for u in db.scalars(select(UnresolvedIngredient).where(UnresolvedIngredient.text.in_(seen))).all()}
    now = utcnow()
    for text, types in seen.items():
        u = existing.get(text)
        if u is None:
            db.add(UnresolvedIngredient(text=text, occurrences=1, source_types=sorted(types), first_seen=now, last_seen=now))
        else:
            u.occurrences += 1
            u.last_seen = now
            u.source_types = sorted(set(u.source_types) | types)
    db.commit()


def analyze_for_user(
    db: Session,
    user: User,
    sources: list[EvidenceSource],
    ctx: AnalysisContext | None = None,
    use_llm: bool = True,
) -> AnalysisResult:
    """Entry point for pipelines: the user's saved allergy profile is always applied."""
    result = assess(get_knowledge_base(db), sources, profile_for(user), ctx)
    record_unresolved(db, result)
    return add_explanation(result, use_llm=use_llm)
