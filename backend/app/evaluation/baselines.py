"""Baselines for the research comparison (Stage 4, plan point 18).

Each baseline reads the same sources as the proposed engine and answers, per allergen, PRESENT or
NOT_LISTED only (none of them can say "unknown"):

  B1 exact      allergen names (and their plural / common singular forms) appear in the text
  B2 synonyms   the curated allergen term list (with look-alike exclusions), after cleaning and spelling
                normalization
  B3 knowledge  canonical concepts and their *definite* relationships (no evidence fusion, no source
                reliability, no uncertainty): any linked allergen counts as present
"""

from __future__ import annotations

import re

from app.engine.knowledge import KnowledgeBase
from app.engine.normalize import apply_spelling, clean, normalize_ingredients
from app.engine.types import EvidenceSource

# The word(s) a naive exact matcher would look for, per allergen category.
EXACT_NAMES = {
    "gluten": ["gluten"], "wheat": ["wheat"], "crustaceans": ["crustacean", "crustaceans"], "eggs": ["egg", "eggs"],
    "fish": ["fish"], "peanuts": ["peanut", "peanuts"], "soy": ["soy", "soya"], "milk": ["milk"],
    "tree_nuts": ["tree nut", "tree nuts", "nut", "nuts"], "celery": ["celery"], "mustard": ["mustard"],
    "sesame": ["sesame"], "sulphites": ["sulphite", "sulphites", "sulfite", "sulfites"], "lupin": ["lupin"],
    "molluscs": ["mollusc", "molluscs", "mollusk", "mollusks"],
}

PRESENT, NOT_LISTED = "PRESENT", "NOT_LISTED_IN_VERIFIED_RECORDS"


def _texts(src: EvidenceSource) -> list[str]:
    out = list(src.ingredients)
    if src.ingredients_text:
        out.append(src.ingredients_text)
    if src.cross_contact_text:
        out.append(src.cross_contact_text)
    return out


def _declared(src: EvidenceSource) -> set[str]:
    return set(src.declared_allergens or []) | {c.allergen_code for c in src.cross_contact}


def b1_exact(kb: KnowledgeBase, sources: list[EvidenceSource], codes: set[str]) -> dict[str, str]:
    text = " ".join(t.lower() for s in sources for t in _texts(s))
    declared = set().union(*(_declared(s) for s in sources)) if sources else set()
    out = {}
    for code in codes:
        hit = code in declared or any(re.search(rf"\b{re.escape(w)}\b", text) for w in EXACT_NAMES.get(code, [code]))
        out[code] = PRESENT if hit else NOT_LISTED
    return out


def b2_synonyms(kb: KnowledgeBase, sources: list[EvidenceSource], codes: set[str]) -> dict[str, str]:
    found = set().union(*(_declared(s) for s in sources)) if sources else set()
    for s in sources:
        for t in _texts(s):
            found |= {m.allergen_code for m in kb.match(apply_spelling(clean(t)))}
    return {c: PRESENT if c in found else NOT_LISTED for c in codes}


def b3_knowledge(kb: KnowledgeBase, sources: list[EvidenceSource], codes: set[str]) -> dict[str, str]:
    found = set().union(*(_declared(s) for s in sources)) if sources else set()
    for s in sources:
        for t in _texts(s):
            for n in normalize_ingredients(kb, t, ocr=False):
                if n.resolution is None:
                    continue
                found |= {m.allergen_code for m in n.resolution.terms}
                found |= {p.allergen_code for _, p in n.resolution.paths if p.certainty == "definite"}
    return {c: PRESENT if c in found else NOT_LISTED for c in codes}


def ingredient_links(method: str, kb: KnowledgeBase, text: str, ocr: bool = False) -> set[str]:
    """Allergens a method links to one ingredient text (for the ingredient-level comparison)."""
    if method == "b1_exact":
        return {c for c, ws in EXACT_NAMES.items() if any(re.search(rf"\b{re.escape(w)}\b", text.lower()) for w in ws)}
    if method == "b2_synonyms":
        return {m.allergen_code for m in kb.match(apply_spelling(clean(text)))}
    from app.engine.assess import resolution_out

    out: set[str] = set()
    for n in normalize_ingredients(kb, text, ocr=ocr if method == "proposed" else False):
        r = resolution_out(kb, "x", n)
        out |= set(r.allergens) | (set(r.possible_allergens) if method == "proposed" else set())
    return out


BASELINES = {"b1_exact": b1_exact, "b2_synonyms": b2_synonyms, "b3_knowledge": b3_knowledge}
