"""Glue between the engine and the database: knowledge base loading and the user's profile."""

from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.engine.assess import assess
from app.engine.explain import add_explanation
from app.engine.knowledge import AllergenInfo, KnowledgeBase
from app.engine.types import AllergyProfile, AnalysisContext, AnalysisResult, EvidenceSource
from app.models import Allergen, AllergenTermKind, User

_kb: KnowledgeBase | None = None
_lock = threading.Lock()


def load_knowledge_base(db: Session) -> KnowledgeBase:
    allergens = db.scalars(select(Allergen).options(selectinload(Allergen.terms))).all()
    synonyms, exclusions = [], []
    for a in allergens:
        for t in a.terms:
            (synonyms if t.kind == AllergenTermKind.synonym else exclusions).append((t.term, a.code))
    return KnowledgeBase([AllergenInfo(a.code, a.name) for a in allergens], synonyms, exclusions)


def get_knowledge_base(db: Session) -> KnowledgeBase:
    """Cached per process; the knowledge base only changes through seeding."""
    global _kb
    with _lock:
        if _kb is None:
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


def analyze_for_user(
    db: Session,
    user: User,
    sources: list[EvidenceSource],
    ctx: AnalysisContext | None = None,
    use_llm: bool = True,
) -> AnalysisResult:
    """Entry point for pipelines: the user's saved allergy profile is always applied."""
    result = assess(get_knowledge_base(db), sources, profile_for(user), ctx)
    return add_explanation(result, use_llm=use_llm)
