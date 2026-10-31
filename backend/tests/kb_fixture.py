"""Build the full seeded knowledge base in memory (no database), for engine tests."""

import json
from pathlib import Path

from app.engine.knowledge import AllergenInfo, ConceptInfo, KnowledgeBase
from app.seed.allergen_data import ALLERGENS
from app.seed.concept_data import ALLERGEN_HIERARCHY, CONCEPTS, MODIFIERS, RELATIONS
from app.seed.regulatory_data import REGULATORY_LISTS

FOODON = json.loads((Path(__file__).parents[1] / "app" / "seed" / "foodon_mappings.json").read_text())["mappings"]


def build_kb() -> KnowledgeBase:
    regulated: dict[str, list[str]] = {}
    for rl in REGULATORY_LISTS:
        for code, _ in rl["entries"]:
            regulated.setdefault(code, []).append(rl["jurisdiction"])
    return KnowledgeBase(
        [AllergenInfo(a["code"], a["name"]) for a in ALLERGENS],
        [(t, a["code"]) for a in ALLERGENS for t in a["synonyms"]],
        [(t, a["code"]) for a in ALLERGENS for t, _ in a["exclusions"]],
        concepts=[ConceptInfo(s, n, amb, FOODON.get(s, {}).get("foodon_id")) for s, (n, _, amb) in CONCEPTS.items()],
        aliases=[(a.lower(), s) for s, (_, al, _) in CONCEPTS.items() for a in al],
        relations=RELATIONS,
        hierarchy=ALLERGEN_HIERARCHY,
        regulated={k: sorted(set(v)) for k, v in regulated.items()},
        modifiers=MODIFIERS,
        version=1,
    )
