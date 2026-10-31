"""Ingredient knowledge base: look-up for any signed-in user; maintenance for knowledge administrators."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentAdmin, CurrentUser
from app.engine.service import get_knowledge_base, knowledge_version
from app.engine.text import is_nonspecific, normalize, split_items
from app.engine.types import IngredientResolution
from app.knowledge import workflow
from app.models import (
    ConceptAlias,
    ConceptRelation,
    IngredientConcept,
    KnowledgeChange,
    KnowledgeProposal,
    KnowledgeStatus,
    KnowledgeVersion,
    ProposalKind,
    ProposalStatus,
    RegulatoryList,
    UnresolvedIngredient,
    UnresolvedStatus,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge base"])


# ---- read ----


class ResolveIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@router.post("/resolve", response_model=list[IngredientResolution])
def resolve(data: ResolveIn, _: CurrentUser, db: DB) -> list[IngredientResolution]:
    """How the knowledge base understands each ingredient in a list (no allergy profile involved)."""
    kb = get_knowledge_base(db)
    out = []
    for item in split_items(normalize(data.text)):
        r = kb.resolve_item(item, nonspecific=is_nonspecific(item))
        concept = kb.concepts.get(r.concepts[0].slug) if r.concepts else None
        definite = [p for _, p in r.paths if p.certainty == "definite"]
        possible = [p for _, p in r.paths if p.certainty == "possible"]
        allergens = list(dict.fromkeys([p.allergen_code for p in definite] + [t.allergen_code for t in r.terms]))
        out.append(IngredientResolution(
            source_id="input", text=item, status=r.status,
            concept=concept.slug if concept else None, concept_name=concept.name if concept else "",
            foodon_id=concept.foodon_id if concept else None,
            allergens=[a for a in allergens if a in kb.allergens],
            possible_allergens=list(dict.fromkeys(p.allergen_code for p in possible if p.allergen_code not in allergens)),
            relationship_path=(definite or possible)[0].text if (definite or possible) else "",
        ))
    return out


@router.get("/regulatory")
def regulatory_lists(_: CurrentUser, db: DB) -> list[dict[str, Any]]:
    """Regional allergen lists, with their legal reference and verification status."""
    return [
        {
            "jurisdiction": rl.jurisdiction, "name": rl.name, "legal_reference": rl.legal_reference,
            "version": rl.version, "status": rl.status.value, "note": rl.note,
            "entries": [{"allergen": e.allergen.code, "label_name": e.label_name} for e in rl.entries],
        }
        for rl in db.scalars(select(RegulatoryList).order_by(RegulatoryList.jurisdiction)).all()
    ]


@router.get("/summary")
def summary(_: CurrentAdmin, db: DB) -> dict[str, Any]:
    count = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w)) or 0  # noqa: E731
    return {
        "version": knowledge_version(db),
        "concepts": count(IngredientConcept),
        "concepts_with_foodon_id": count(IngredientConcept, IngredientConcept.foodon_id.is_not(None)),
        "aliases": count(ConceptAlias),
        "relations": count(ConceptRelation, ConceptRelation.status != KnowledgeStatus.retired),
        "pending_proposals": count(KnowledgeProposal, KnowledgeProposal.status == ProposalStatus.pending),
        "open_unresolved": count(UnresolvedIngredient, UnresolvedIngredient.status == UnresolvedStatus.open),
        "versions": [
            {"version": v.version, "note": v.note, "created_at": v.created_at}
            for v in db.scalars(select(KnowledgeVersion).order_by(KnowledgeVersion.version.desc()).limit(20)).all()
        ],
    }


def _concept_out(db: DB, c: IngredientConcept, detail: bool = False) -> dict[str, Any]:
    out = {
        "slug": c.slug, "name": c.name, "ambiguous_source": c.ambiguous_source, "status": c.status.value,
        "foodon_id": c.foodon_id, "foodon_label": c.foodon_label, "foodon_note": c.foodon_note,
        "source": c.source, "added_in_version": c.added_in_version,
        "aliases": sorted(a.alias for a in c.aliases if a.status != KnowledgeStatus.retired),
    }
    if detail:
        rels = db.scalars(select(ConceptRelation).where(
            or_(ConceptRelation.subject_id == c.id, ConceptRelation.object_concept_id == c.id))).all()
        out["relations"] = [
            {"id": r.id, "subject": r.subject.slug, "relation": r.relation.value,
             "object": r.object_concept.slug if r.object_concept else f"allergen:{r.object_allergen.code}",
             "certainty": r.certainty.value, "status": r.status.value, "source": r.source, "added_in_version": r.added_in_version}
            for r in rels
        ]
        kb = get_knowledge_base(db)
        out["allergen_paths"] = [
            {"allergen": p.allergen_code, "certainty": p.certainty, "path": p.text}
            for p in (kb.allergen_paths(c.slug) if c.slug in kb.concepts else [])
        ]
    return out


@router.get("/concepts")
def list_concepts(_: CurrentAdmin, db: DB, q: str = "", limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    stmt = select(IngredientConcept)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(IngredientConcept.slug.ilike(like), IngredientConcept.name.ilike(like),
                              IngredientConcept.id.in_(select(ConceptAlias.concept_id).where(ConceptAlias.alias.ilike(like)))))
    return [_concept_out(db, c) for c in db.scalars(stmt.order_by(IngredientConcept.slug).limit(limit)).all()]


@router.get("/concepts/{slug}")
def get_concept(slug: str, _: CurrentAdmin, db: DB) -> dict[str, Any]:
    c = db.scalar(select(IngredientConcept).where(IngredientConcept.slug == slug))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Concept not found.")
    return _concept_out(db, c, detail=True)


@router.get("/changes")
def list_changes(_: CurrentAdmin, db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    rows = db.scalars(select(KnowledgeChange).order_by(KnowledgeChange.id.desc()).limit(limit)).all()
    return [{"id": c.id, "version": c.version, "action": c.action, "entity_type": c.entity_type, "entity_id": c.entity_id,
             "details": c.details, "actor_id": c.actor_id, "proposal_id": c.proposal_id, "created_at": c.created_at} for c in rows]


# ---- proposals ----


class ProposalIn(BaseModel):
    kind: ProposalKind
    payload: dict[str, Any]
    reason: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=2000)


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)


def _proposal_out(p: KnowledgeProposal) -> dict[str, Any]:
    return {"id": p.id, "kind": p.kind.value, "payload": p.payload, "reason": p.reason, "source": p.source,
            "status": p.status.value, "proposed_by_id": p.proposed_by_id, "decided_by_id": p.decided_by_id,
            "decided_at": p.decided_at, "decision_note": p.decision_note, "resulting_version": p.resulting_version,
            "created_at": p.created_at}


@router.post("/proposals", status_code=status.HTTP_201_CREATED)
def create_proposal(data: ProposalIn, admin: CurrentAdmin, db: DB) -> dict[str, Any]:
    return _proposal_out(workflow.propose(db, admin, data.kind, data.payload, data.reason, data.source))


@router.get("/proposals")
def list_proposals(_: CurrentAdmin, db: DB, proposal_status: Annotated[ProposalStatus | None, Query(alias="status")] = None) -> list[dict[str, Any]]:
    stmt = select(KnowledgeProposal).order_by(KnowledgeProposal.id.desc())
    if proposal_status:
        stmt = stmt.where(KnowledgeProposal.status == proposal_status)
    return [_proposal_out(p) for p in db.scalars(stmt).all()]


@router.post("/proposals/{proposal_id}/decision")
def decide_proposal(proposal_id: int, data: DecisionIn, admin: CurrentAdmin, db: DB) -> dict[str, Any]:
    p = db.get(KnowledgeProposal, proposal_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found.")
    return _proposal_out(workflow.decide(db, admin, p, data.approve, data.note))


# ---- unresolved ingredients ----


class UnresolvedDecisionIn(BaseModel):
    status: UnresolvedStatus
    concept: str | None = None
    note: str = Field(default="", max_length=2000)


@router.get("/unresolved")
def list_unresolved(_: CurrentAdmin, db: DB, unresolved_status: Annotated[UnresolvedStatus, Query(alias="status")] = UnresolvedStatus.open,
                    limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    rows = db.scalars(select(UnresolvedIngredient).where(UnresolvedIngredient.status == unresolved_status)
                      .order_by(UnresolvedIngredient.occurrences.desc(), UnresolvedIngredient.last_seen.desc()).limit(limit)).all()
    return [{"id": u.id, "text": u.text, "occurrences": u.occurrences, "source_types": u.source_types,
             "first_seen": u.first_seen, "last_seen": u.last_seen, "status": u.status.value, "note": u.note} for u in rows]


@router.post("/unresolved/{item_id}")
def review_unresolved(item_id: int, data: UnresolvedDecisionIn, _: CurrentAdmin, db: DB) -> dict[str, Any]:
    u = db.get(UnresolvedIngredient, item_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    if data.status == UnresolvedStatus.resolved:
        c = db.scalar(select(IngredientConcept).where(IngredientConcept.slug == (data.concept or "")))
        if c is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name the concept that now covers this text (add it through a proposal first).")
        u.resolved_concept_id = c.id
    u.status, u.note = data.status, data.note
    db.commit()
    return {"id": u.id, "status": u.status.value}
