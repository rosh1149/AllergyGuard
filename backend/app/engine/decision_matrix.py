"""The evidence-decision matrix, published from the values the engine actually uses."""

from __future__ import annotations

from app.core.config import get_settings
from app.engine.rules import RULES
from app.engine.assess import LIST_MISMATCH_THRESHOLD
from app.engine.types import MIN_IDENTITY_CONFIDENCE, MIN_SOURCE_CONFIDENCE, SOURCE_POLICY, SOURCE_TYPE_LABELS

ABSENCE_CONDITIONS = [
    "The source is a kind that can support absence (see sources).",
    f"It belongs to the item checked: identification confidence at least {MIN_IDENTITY_CONFIDENCE}, unless it is the customer's own label.",
    "It states the full ingredient list.",
    f"It was read reliably: confidence at least {MIN_SOURCE_CONFIDENCE}.",
    "It lists at least one ingredient.",
    "It is within its freshness limit.",
    "Every ingredient in it was identified.",
    "No part of its list is cut off or open-ended.",
    "No two eligible sources' ingredient lists clearly disagree.",
    "And no evidence of any kind (present, cross-contact, candidate, possible source, free-from claim) exists for the allergen.",
]

CONFLICT_HANDLING = {
    "cautious_finding_kept": "Sources disagree about an allergen; the finding that the allergen is present is kept.",
    "absence_blocked": "The ingredient lists differ substantially; neither is used to rule allergens out (unresolved).",
    "no_allergen_effect": "The lists differ only in ingredients without any allergen link; no finding changes.",
    "cross_contact_kept": "One source records cross-contact and another none; possible cross-contact is reported.",
}

CROSS_CONTACT_STATUS = {
    "reported": "A source records cross-contact or a precautionary statement names the allergen.",
    "none_reported": "A trusted source states that cross-contact was assessed and records none for the allergen.",
    "not_assessed": "No source says whether cross-contact was assessed.",
}


def decision_matrix() -> dict:
    s = get_settings()
    return {
        "sources": [
            {"type": t.value, "label": SOURCE_TYPE_LABELS[t], "shows_present": p.shows_present,
             "supports_absence": p.supports_absence, "depends_on_identification": p.depends_on_identification,
             "freshness": p.freshness, "role": p.role}
            for t, p in SOURCE_POLICY.items()
        ],
        "outcome_rules": RULES,
        "absence_conditions": ABSENCE_CONDITIONS,
        "thresholds": {
            "min_identity_confidence": MIN_IDENTITY_CONFIDENCE,
            "min_source_confidence": MIN_SOURCE_CONFIDENCE,
            "list_mismatch_overlap_below": LIST_MISMATCH_THRESHOLD,
            "restaurant_record_max_age_days": s.restaurant_record_max_age_days,
            "product_record_max_age_days": s.product_record_max_age_days,
        },
        "conflict_handling": CONFLICT_HANDLING,
        "cross_contact_status": CROSS_CONTACT_STATUS,
        "never": "No outcome certifies a food as safe. Confidence values decide eligibility only; they are never "
                 "combined into a score that rules an allergen out.",
    }
