"""Structural rules for the ingredient knowledge graph.

Every approved knowledge change, and the seed data itself, must pass these rules. They check that
relations point the right way between the right kinds of concepts, so a wrong-direction or circular
relationship cannot quietly create (or hide) an allergen link.
"""

from __future__ import annotations

from app.models.knowledge import ConceptType

TYPES = {t.value for t in ConceptType}


def a(word: str) -> str:
    return ("an " if word[:1] in "aeiou" else "a ") + word.replace("_", " ")
ALLERGEN_RELATIONS = {"is_regulated_as", "associated_with_allergen"}
CONCEPT_RELATIONS = {"derived_from", "is_source_of", "is_a", "contains_component", "contains_protein"}

# Who may be regulated as an allergen category: sources, proteins and regulated substances.
REGULATED_SUBJECTS = {"food_source", "protein", "allergen_substance"}
# Things a product can be derived from: not a made food, a vague term or an additive.
DERIVABLE_FROM = {"food_source", "ingredient", "derivative", "protein", "allergen_substance"}
CONTAINS_PROTEIN_SUBJECTS = {"food_source", "ingredient", "derivative"}


def validate_graph(
    concepts: dict[str, tuple[str, bool]],  # slug -> (concept type, ambiguous_source)
    relations: list[tuple[str, str, str, str]],  # (subject, relation, object, certainty)
    allergen_codes: set[str],
) -> list[str]:
    errors: list[str] = []
    for slug, (ctype, _) in concepts.items():
        if ctype not in TYPES:
            errors.append(f"{slug}: unknown concept type '{ctype}'")

    edges: dict[str, list[str]] = {}
    ambiguous_definite: list[tuple[str, str]] = []
    for subj, rel, obj, cert in relations:
        where = f"{subj} {rel} {obj}"
        if subj not in concepts:
            errors.append(f"{where}: unknown subject")
            continue
        stype, ambiguous = concepts[subj]
        if cert not in ("definite", "possible"):
            errors.append(f"{where}: certainty must be definite or possible")
        if rel in ALLERGEN_RELATIONS:
            code = obj.removeprefix("allergen:")
            if not obj.startswith("allergen:") or code not in allergen_codes:
                errors.append(f"{where}: object must be an allergen category")
            if rel == "is_regulated_as" and stype not in REGULATED_SUBJECTS:
                errors.append(f"{where}: only a food source, protein or regulated substance is regulated as an "
                              f"allergen category (not {a(stype)})")
            continue
        if rel not in CONCEPT_RELATIONS:
            errors.append(f"{where}: unknown relation")
            continue
        if obj not in concepts:
            errors.append(f"{where}: unknown object")
            continue
        if obj == subj:
            errors.append(f"{where}: a concept cannot relate to itself")
            continue
        otype = concepts[obj][0]
        if rel == "is_a" and otype != stype:
            errors.append(f"{where}: is_a links concepts of the same type ({stype} vs {otype})")
        if rel == "derived_from":
            if otype not in DERIVABLE_FROM:
                errors.append(f"{where}: cannot be derived from {a(otype)}")
            if stype == "food_source" and otype != "food_source":
                errors.append(f"{where}: a food source is not derived from {a(otype)} (direction reversed?)")
        if rel == "is_source_of" and concepts[subj][0] not in ("food_source", "ingredient", "derivative"):
            errors.append(f"{where}: only a source, ingredient or derivative is a source of something")
        if rel == "contains_protein":
            if otype != "protein":
                errors.append(f"{where}: object must be a protein (not {a(otype)})")
            if stype not in CONTAINS_PROTEIN_SUBJECTS:
                errors.append(f"{where}: {a(stype)} does not contain a protein in this sense")
        if stype == "general_term" and cert == "definite":
            errors.append(f"{where}: a general term (its contents are not stated) can only possibly contain something")
        elif ambiguous and cert == "definite" and rel in ("contains_component", "derived_from"):
            ambiguous_definite.append((where, obj))
        # Edges in the direction inference follows (ingredient -> towards its sources).
        if rel == "is_source_of":
            edges.setdefault(obj, []).append(subj)
        elif rel == "contains_protein":
            edges.setdefault(obj, []).append(subj)
        else:
            edges.setdefault(subj, []).append(obj)

    # No cycles: following relationships must never lead back to where it started.
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        state[node] = 1
        for nxt in edges.get(node, []):
            if state.get(nxt) == 1:
                cycle = trail[trail.index(nxt):] + [nxt] if nxt in trail else [node, nxt]
                errors.append("cycle: " + " -> ".join(cycle))
            elif state.get(nxt) is None:
                visit(nxt, trail + [nxt])
        state[node] = 2

    for n in list(edges):
        if state.get(n) is None:
            visit(n, [n])

    # An unstated-source concept (lecithin, chocolate) may definitely contain things, but never anything
    # that leads to an allergen: which allergen depends on the source, which the name does not state.
    to_allergen = {s for s, r, _, _ in relations if r in ALLERGEN_RELATIONS}

    def leads_to_allergen(node: str, seen: set[str]) -> bool:
        if node in to_allergen:
            return True
        seen.add(node)
        return any(n not in seen and leads_to_allergen(n, seen) for n in edges.get(node, []))

    for where, obj in ambiguous_definite:
        if leads_to_allergen(obj, set()):
            errors.append(f"{where}: an unstated-source concept cannot definitely lead to an allergen")

    reached = {o.removeprefix("allergen:") for _, r, o, _ in relations if r in ALLERGEN_RELATIONS}
    for code in sorted(allergen_codes - reached):
        errors.append(f"allergen category '{code}' is not reached by any concept")
    return list(dict.fromkeys(errors))


def validate_kb(kb) -> list[str]:
    return validate_graph(
        {s: (c.concept_type, c.ambiguous_source) for s, c in kb.concepts.items()},
        kb.relations, set(kb.allergens),
    )
