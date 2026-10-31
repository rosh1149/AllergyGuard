"""Controlled changes to the ingredient knowledge base.

A change is first a proposal. Approving it applies the change inside a transaction, rebuilds the
knowledge base from that uncommitted state and runs the regression cases (KB_REGRESSION_CASES); if any
case fails, nothing is committed. A successful approval publishes a new knowledge version and writes
the change history, so every assessment can be traced to the version it used.
"""

from __future__ import annotations

import re

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.service import load_knowledge_base, reset_knowledge_base_cache
from app.models import (
    Allergen,
    AllergenTerm,
    AllergenTermKind,
    Certainty,
    ConceptAlias,
    ConceptType,
    ConceptRelation,
    IngredientConcept,
    KnowledgeChange,
    KnowledgeProposal,
    KnowledgeStatus,
    KnowledgeVersion,
    ProposalKind,
    ProposalStatus,
    RegulatoryEntry,
    RegulatoryList,
    RelationType,
    User,
)
from app.knowledge.validation import ALLERGEN_RELATIONS, validate_kb
from app.models.common import utcnow
from app.seed.kb_regression import KB_REGRESSION_CASES
from app.seed.knowledge_seed import current_version

SLUG = re.compile(r"^[a-z0-9_]{2,80}$")
FOODON_ID = re.compile(r"^FOODON:\d{8}$")


def _422(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, msg)


def _concept(db: Session, slug: str) -> IngredientConcept:
    c = db.scalar(select(IngredientConcept).where(IngredientConcept.slug == slug))
    if c is None:
        raise _422(f"Unknown concept: {slug}")
    return c


def _allergen(db: Session, code: str) -> Allergen:
    a = db.scalar(select(Allergen).where(Allergen.code == code))
    if a is None:
        raise _422(f"Unknown allergen: {code}")
    return a


def _text(payload: dict, key: str, max_len: int = 200) -> str:
    v = " ".join(str(payload.get(key, "")).lower().split())
    if not v or len(v) > max_len:
        raise _422(f"'{key}' is required (at most {max_len} characters).")
    return v


def validate(db: Session, kind: ProposalKind, payload: dict) -> None:
    """Checks that do not depend on other pending proposals; run when proposing and again when approving."""
    if kind == ProposalKind.concept:
        slug = str(payload.get("slug", ""))
        if not SLUG.match(slug):
            raise _422("Concept slug must be 2-80 characters of a-z, 0-9 and _.")
        if db.scalar(select(IngredientConcept.id).where(IngredientConcept.slug == slug)):
            raise _422(f"Concept '{slug}' already exists.")
        _text(payload, "name")
        if payload.get("foodon_id") and not FOODON_ID.match(payload["foodon_id"]):
            raise _422("FoodOn id must look like FOODON:03420180.")
        if payload.get("concept_type", "ingredient") not in ConceptType.__members__:
            raise _422(f"concept_type must be one of: {', '.join(ConceptType.__members__)}.")
    elif kind == ProposalKind.foodon_mapping:
        _concept(db, str(payload.get("concept", "")))
        fid = payload.get("foodon_id")
        if fid is not None and not FOODON_ID.match(str(fid)):
            raise _422("FoodOn id must look like FOODON:03420180, or null to remove the mapping.")
        if not str(payload.get("evidence", "")).strip():
            raise _422("Say why: the FoodOn label and release that justify this mapping (or why it is wrong).")
    elif kind == ProposalKind.alias:
        _concept(db, str(payload.get("concept", "")))
        alias = _text(payload, "alias")
        other = db.scalar(select(ConceptAlias).where(ConceptAlias.alias == alias))
        if other is not None:
            raise _422(f"'{alias}' is already an alias of concept '{other.concept.slug}'.")
    elif kind == ProposalKind.relation:
        rel = payload.get("relation")
        if rel not in RelationType.__members__:
            raise _422(f"Relation must be one of: {', '.join(RelationType.__members__)}.")
        if payload.get("certainty", "definite") not in Certainty.__members__:
            raise _422("Certainty must be 'definite' or 'possible'.")
        subj = _concept(db, str(payload.get("subject", "")))
        if rel in ALLERGEN_RELATIONS:
            obj_key = "allergen:" + _allergen(db, str(payload.get("allergen", ""))).code
        else:
            obj = _concept(db, str(payload.get("object", "")))
            if obj.id == subj.id:
                raise _422("A concept cannot be related to itself.")
            obj_key = obj.slug
        # Structural rules (types, direction, cycles) checked against the knowledge base plus this relation.
        kb = load_knowledge_base(db)
        before = set(validate_kb(kb))
        kb.relations.append((subj.slug, rel, obj_key, payload.get("certainty", "definite")))
        new = [e for e in validate_kb(kb) if e not in before]
        if new:
            raise _422("The relation breaks knowledge-graph rules: " + "; ".join(new))
    elif kind == ProposalKind.allergen_term:
        _allergen(db, str(payload.get("allergen", "")))
        _text(payload, "term", 120)
        if payload.get("kind") not in ("synonym", "exclusion"):
            raise _422("Term kind must be 'synonym' or 'exclusion'.")
    elif kind == ProposalKind.regulatory_entry:
        _allergen(db, str(payload.get("allergen", "")))
        if not db.scalar(select(RegulatoryList.id).where(RegulatoryList.jurisdiction == payload.get("jurisdiction"))):
            raise _422("Unknown jurisdiction.")
        _text(payload, "label_name", 500)
    elif kind == ProposalKind.retire_relation:
        if db.get(ConceptRelation, int(payload.get("relation_id", 0) or 0)) is None:
            raise _422("Unknown relation id.")


def _apply(db: Session, p: KnowledgeProposal, version: int) -> tuple[str, int | None]:
    """Apply an approved proposal. Returns (entity type, entity id) for the change log."""
    d, src = p.payload, p.source or f"Knowledge proposal #{p.id}"
    if p.kind == ProposalKind.concept:
        c = IngredientConcept(
            slug=d["slug"], name=_text(d, "name"), description=str(d.get("description", "")),
            ambiguous_source=bool(d.get("ambiguous_source", False)), foodon_id=d.get("foodon_id") or None,
            concept_type=d.get("concept_type", "ingredient"),
            foodon_note=f"Provided by reviewer; {src}" if d.get("foodon_id") else "",
            status=KnowledgeStatus.verified, source=src, added_in_version=version,
        )
        db.add(c)
        db.flush()
        for a in dict.fromkeys(" ".join(str(x).lower().split()) for x in [c.name, *d.get("aliases", [])]):
            if a:
                db.add(ConceptAlias(concept_id=c.id, alias=a, status=KnowledgeStatus.verified, source=src, added_in_version=version))
        return "concept", c.id
    if p.kind == ProposalKind.foodon_mapping:
        c = _concept(db, d["concept"])
        p.payload = {**d, "previous_foodon_id": c.foodon_id}  # kept in the change log
        c.foodon_id = d.get("foodon_id") or None
        c.foodon_label = d.get("foodon_label") or None
        c.foodon_note = (f"Set by proposal #{p.id} in knowledge version {version}: {d['evidence']}" if c.foodon_id
                         else f"Mapping removed by proposal #{p.id} in knowledge version {version}: {d['evidence']}")
        return "ingredient_concept", c.id
    if p.kind == ProposalKind.alias:
        a = ConceptAlias(concept_id=_concept(db, d["concept"]).id, alias=_text(d, "alias"),
                         status=KnowledgeStatus.verified, source=src, added_in_version=version)
        db.add(a)
        db.flush()
        return "concept_alias", a.id
    if p.kind == ProposalKind.relation:
        r = ConceptRelation(
            subject_id=_concept(db, d["subject"]).id, relation=RelationType(d["relation"]),
            object_concept_id=_concept(db, d["object"]).id if d["relation"] not in ALLERGEN_RELATIONS else None,
            object_allergen_id=_allergen(db, d["allergen"]).id if d["relation"] in ALLERGEN_RELATIONS else None,
            certainty=Certainty(d.get("certainty", "definite")), status=KnowledgeStatus.verified, source=src,
            added_in_version=version,
        )
        db.add(r)
        db.flush()
        return "concept_relation", r.id
    if p.kind == ProposalKind.allergen_term:
        t = AllergenTerm(allergen_id=_allergen(db, d["allergen"]).id, term=_text(d, "term", 120),
                         kind=AllergenTermKind(d["kind"]), note=f"Added in knowledge version {version}; {src}")
        db.add(t)
        db.flush()
        return "allergen_term", t.id
    if p.kind == ProposalKind.regulatory_entry:
        rl = db.scalars(select(RegulatoryList).where(RegulatoryList.jurisdiction == d["jurisdiction"]).order_by(RegulatoryList.id.desc())).first()
        e = RegulatoryEntry(list_id=rl.id, allergen_id=_allergen(db, d["allergen"]).id, label_name=str(d["label_name"]))
        db.add(e)
        db.flush()
        return "regulatory_entry", e.id
    r = db.get(ConceptRelation, int(d["relation_id"]))
    r.status = KnowledgeStatus.retired
    return "concept_relation", r.id


def regression_failures(db: Session) -> list[str]:
    """Run the regression cases against the knowledge base as it stands in this session."""
    kb = load_knowledge_base(db)
    failures = [f"Knowledge-graph rule: {e}" for e in validate_kb(kb)]
    for text, expected in KB_REGRESSION_CASES:
        res = kb.resolve_item(text)
        got = {t.allergen_code for t in res.terms} | {p.allergen_code for _, p in res.paths if p.certainty == "definite"}
        if got != expected:
            failures.append(f"'{text}': expected {sorted(expected) or 'no allergen'}, got {sorted(got) or 'no allergen'}")
    # The safety part of the evaluation benchmark (Step F): no lost allergen links, no unsafe absence.
    from app.evaluation.gate import critical_failures
    from app.evaluation.runner import evaluate

    failures += [f"Safety benchmark: {f}" for f in critical_failures(evaluate(kb))]
    return failures


def propose(db: Session, user: User, kind: ProposalKind, payload: dict, reason: str, source: str) -> KnowledgeProposal:
    validate(db, kind, payload)
    p = KnowledgeProposal(kind=kind, payload=payload, reason=reason, source=source, proposed_by_id=user.id)
    db.add(p)
    db.commit()
    return p


def decide(db: Session, reviewer: User, p: KnowledgeProposal, approve: bool, note: str) -> KnowledgeProposal:
    if p.status != ProposalStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This proposal was already {p.status.value}.")
    if get_settings().knowledge_require_second_reviewer and p.proposed_by_id == reviewer.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A different reviewer must decide on this proposal.")
    if not approve:
        p.status, p.decided_by_id, p.decided_at, p.decision_note = ProposalStatus.rejected, reviewer.id, utcnow(), note
        db.commit()
        return p

    validate(db, p.kind, p.payload)
    version = current_version(db) + 1
    entity_type, entity_id = _apply(db, p, version)
    db.flush()
    failures = regression_failures(db)
    if failures:
        db.rollback()
        p = db.get(KnowledgeProposal, p.id)
        p.decision_note = "Not published: regression cases failed: " + "; ".join(failures)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "The change breaks knowledge regression cases.", "failures": failures})

    db.add(KnowledgeVersion(version=version, note=f"{p.kind.value} proposal #{p.id}" + (f": {p.reason}" if p.reason else ""),
                            created_by_id=reviewer.id))
    db.add(KnowledgeChange(version=version, action=f"{p.kind.value}.approved", entity_type=entity_type, entity_id=entity_id,
                           details={"payload": p.payload, "reason": p.reason, "source": p.source, "note": note},
                           actor_id=reviewer.id, proposal_id=p.id))
    p.status, p.decided_by_id, p.decided_at, p.decision_note, p.resulting_version = (
        ProposalStatus.approved, reviewer.id, utcnow(), note, version)
    db.commit()
    reset_knowledge_base_cache()
    return p
