"""Named decision rules, so every outcome records which rule produced it.

Order matters: the first matching rule decides the outcome (most cautious first). The same order is
implemented in `assess._decide`; `rules_for` only names the branch that was taken and the conditions
that blocked absence."""

from __future__ import annotations

from app.engine.types import EvidenceRef, Outcome

RULES = {
    "R1.present_ingredient": "An ingredient in an authoritative source indicates the allergen.",
    "R1.present_declared": "An authoritative source declares the allergen.",
    "R2.cross_contact_declared": "A trusted source declares possible or known cross-contact.",
    "R2.cross_contact_candidate": "An unverified or uncertain source reports cross-contact.",
    "R3.unverified_candidate": "Only an unverified or possibly-mismatched source suggests the allergen.",
    "R3.possible_source": "An ingredient may come from an allergen source that is not stated.",
    "R4.free_from_claim": "A free-from claim cannot be verified, so it never establishes absence.",
    "R5.not_listed_verified": "Complete, fresh, verified sources were checked and do not list the allergen.",
    "R6.absence_not_supported": "No evidence of the allergen, but absence cannot be established.",
    # Conditions recorded alongside R6 (why absence was not supported).
    "B.no_eligible_source": "No source was complete, verified, fresh, identified and fully readable.",
    "B.list_mismatch": "The available ingredient lists disagree.",
}


def rules_for(outcome: Outcome, refs: list[EvidenceRef], eligible: bool, list_mismatch: bool) -> list[str]:
    kinds = {r.kind for r in refs}
    if outcome == Outcome.PRESENT:
        out = []
        if "presence" in kinds:
            out.append("R1.present_ingredient")
        if "declared" in kinds:
            out.append("R1.present_declared")
        return out
    if outcome == Outcome.POSSIBLE_CROSS_CONTACT:
        return (["R2.cross_contact_declared"] if "cross_contact" in kinds else []) + (
            ["R2.cross_contact_candidate"] if "candidate_cross_contact" in kinds else [])
    if outcome == Outcome.NOT_LISTED_IN_VERIFIED_RECORDS:
        return ["R5.not_listed_verified"]
    out = []
    if "candidate" in kinds:
        out.append("R3.unverified_candidate")
    if "possible_source" in kinds:
        out.append("R3.possible_source")
    if "claim" in kinds and not out:
        out.append("R4.free_from_claim")
    if not out:
        out.append("R6.absence_not_supported")
        out.append("B.list_mismatch" if eligible and list_mismatch else "B.no_eligible_source")
    return out
