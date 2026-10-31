"""Idempotent seeding of the ingredient knowledge layer (concepts, relations, hierarchy, regional lists).

A fresh database gets knowledge version 1. If a later code release adds seed records to an existing
database, they are inserted under a new version with a change-log entry, so assessments stay traceable.
"""

import json
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Allergen,
    AllergenParent,
    Certainty,
    ConceptAlias,
    ConceptRelation,
    IngredientConcept,
    KnowledgeChange,
    KnowledgeStatus,
    KnowledgeVersion,
    RegulatoryEntry,
    RegulatoryList,
    RelationType,
)
from app.seed.concept_data import ALLERGEN_HIERARCHY, CONCEPTS, RELATIONS
from app.seed.regulatory_data import REGULATORY_LISTS

log = logging.getLogger(__name__)

SEED_SOURCE = "AllergyGuard curated seed"
FOODON = json.loads((Path(__file__).parent / "foodon_mappings.json").read_text())


def current_version(db: Session) -> int:
    return db.scalar(select(func.max(KnowledgeVersion.version))) or 0


def seed_knowledge(db: Session) -> None:
    allergens = {a.code: a for a in db.scalars(select(Allergen)).all()}
    existing_version = current_version(db)
    target = existing_version or 1
    added: dict[str, int] = {"concepts": 0, "aliases": 0, "relations": 0, "hierarchy": 0, "regulatory_entries": 0}

    concepts = {c.slug: c for c in db.scalars(select(IngredientConcept)).all()}
    for slug, (name, aliases, ambiguous) in CONCEPTS.items():
        c = concepts.get(slug)
        if c is None:
            fo = FOODON["mappings"].get(slug)
            c = IngredientConcept(
                slug=slug,
                name=name,
                ambiguous_source=ambiguous,
                foodon_id=fo["foodon_id"] if fo else None,
                foodon_label=fo["foodon_label"] if fo else None,
                foodon_note=(
                    f"Exact label match in FoodOn (commit {FOODON['foodon_commit'][:7]}, {FOODON['foodon_date']}); "
                    f"parent: {', '.join(fo['foodon_parents'])}" if fo else
                    (FOODON["excluded"][slug]["reason"] if slug in FOODON["excluded"] else "No exact FoodOn label match")
                ),
                status=KnowledgeStatus.curated,
                source=SEED_SOURCE,
                added_in_version=target + (1 if existing_version else 0),
            )
            db.add(c)
            db.flush()
            concepts[slug] = c
            added["concepts"] += 1
        have = {a.alias for a in c.aliases}
        for alias in dict.fromkeys(a.lower() for a in aliases):
            if alias not in have:
                c.aliases.append(ConceptAlias(alias=alias, status=KnowledgeStatus.curated, source=SEED_SOURCE,
                                              added_in_version=c.added_in_version))
                added["aliases"] += 1
    db.flush()

    rels = {(r.subject_id, r.relation, r.object_concept_id, r.object_allergen_id) for r in db.scalars(select(ConceptRelation)).all()}
    for subj, rel, obj, certainty in RELATIONS:
        s = concepts[subj]
        if obj.startswith("allergen:"):
            key = (s.id, RelationType(rel), None, allergens[obj.split(":", 1)[1]].id)
        else:
            key = (s.id, RelationType(rel), concepts[obj].id, None)
        if key in rels:
            continue
        db.add(ConceptRelation(subject_id=key[0], relation=key[1], object_concept_id=key[2], object_allergen_id=key[3],
                               certainty=Certainty(certainty), status=KnowledgeStatus.curated, source=SEED_SOURCE,
                               added_in_version=target + (1 if existing_version else 0)))
        rels.add(key)
        added["relations"] += 1

    edges = {(h.child_id, h.parent_id) for h in db.scalars(select(AllergenParent)).all()}
    for child, parent in ALLERGEN_HIERARCHY:
        key = (allergens[child].id, allergens[parent].id)
        if key not in edges:
            db.add(AllergenParent(child_id=key[0], parent_id=key[1], status=KnowledgeStatus.curated))
            added["hierarchy"] += 1

    lists = {(rl.jurisdiction, rl.version): rl for rl in db.scalars(select(RegulatoryList)).all()}
    for spec in REGULATORY_LISTS:
        rl = lists.get((spec["jurisdiction"], spec["version"]))
        if rl is None:
            rl = RegulatoryList(jurisdiction=spec["jurisdiction"], name=spec["name"], legal_reference=spec["legal_reference"],
                                version=spec["version"], status=KnowledgeStatus(spec["status"]), note=spec["note"])
            db.add(rl)
            db.flush()
        have = {e.allergen_id for e in rl.entries}
        for code, label in spec["entries"]:
            if allergens[code].id not in have:
                rl.entries.append(RegulatoryEntry(allergen_id=allergens[code].id, label_name=label))
                added["regulatory_entries"] += 1

    if any(added.values()):
        version = 1 if not existing_version else existing_version + 1
        db.add(KnowledgeVersion(version=version, note="Initial curated knowledge base" if version == 1 else "Seed update"))
        db.add(KnowledgeChange(version=version, action="seed", entity_type="knowledge_base", details=added))
        log.info("Knowledge base version %s seeded: %s", version, added)
    db.commit()
