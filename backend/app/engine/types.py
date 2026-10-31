from __future__ import annotations

import enum
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class Outcome(str, enum.Enum):
    PRESENT = "PRESENT"
    POSSIBLE_CROSS_CONTACT = "POSSIBLE_CROSS_CONTACT"
    UNKNOWN = "UNKNOWN"
    NOT_LISTED_IN_VERIFIED_RECORDS = "NOT_LISTED_IN_VERIFIED_RECORDS"

    @property
    def label(self) -> str:
        return OUTCOME_LABELS[self]


OUTCOME_LABELS = {
    Outcome.PRESENT: "Present",
    Outcome.POSSIBLE_CROSS_CONTACT: "Possible cross-contact",
    Outcome.UNKNOWN: "Unknown",
    Outcome.NOT_LISTED_IN_VERIFIED_RECORDS: "Not listed in verified records",
}

# Most to least severe. The overall result is the most severe outcome among the user's allergens.
SEVERITY = [
    Outcome.PRESENT,
    Outcome.POSSIBLE_CROSS_CONTACT,
    Outcome.UNKNOWN,
    Outcome.NOT_LISTED_IN_VERIFIED_RECORDS,
]


class SourceType(str, enum.Enum):
    label_ocr = "label_ocr"  # ingredient text read from the customer's label photo
    label_manual = "label_manual"  # label text typed in by the customer
    product_database = "product_database"  # e.g. Open Food Facts
    manufacturer = "manufacturer"  # official manufacturer page
    restaurant_verified = "restaurant_verified"  # staff-confirmed recipe version
    restaurant_unverified = "restaurant_unverified"  # staff-entered, not yet confirmed
    public_recipe = "public_recipe"  # web recipe for an unregistered restaurant (candidate only)
    customer_suggestion = "customer_suggestion"  # unverified customer feedback (candidate only)


# Sources whose presence findings count as PRESENT. Everything else is a candidate only.
AUTHORITATIVE = {
    SourceType.label_ocr,
    SourceType.label_manual,
    SourceType.product_database,
    SourceType.manufacturer,
    SourceType.restaurant_verified,
    SourceType.restaurant_unverified,
}

# Sources that, when complete and reliable, can support NOT LISTED IN VERIFIED RECORDS.
ABSENCE_CAPABLE = {
    SourceType.label_ocr,
    SourceType.label_manual,
    SourceType.product_database,
    SourceType.manufacturer,
    SourceType.restaurant_verified,
}

# Sources that describe *a* product/dish, so they're only relevant if identification was right.
# Label text comes straight from the customer's photo, so it doesn't depend on identification.
IDENTITY_DEPENDENT = {
    SourceType.product_database,
    SourceType.manufacturer,
    SourceType.restaurant_verified,
    SourceType.restaurant_unverified,
}

SOURCE_TYPE_LABELS = {
    SourceType.label_ocr: "Package label (read from photo)",
    SourceType.label_manual: "Package label (typed by you)",
    SourceType.product_database: "Product database",
    SourceType.manufacturer: "Manufacturer",
    SourceType.restaurant_verified: "Restaurant records (verified)",
    SourceType.restaurant_unverified: "Restaurant records (not yet verified)",
    SourceType.public_recipe: "Public recipe (unverified, not this restaurant)",
    SourceType.customer_suggestion: "Customer suggestion (unverified)",
}

MIN_IDENTITY_CONFIDENCE = 0.7
MIN_SOURCE_CONFIDENCE = 0.6


# ---- Engine inputs (built by pipelines) ----


@dataclass
class CrossContactDecl:
    allergen_code: str
    level: str = "possible"  # "known" | "possible"
    description: str = ""


@dataclass
class EvidenceSource:
    id: str
    type: SourceType
    name: str
    # Either a raw ingredient statement (label text) or pre-split items (restaurant records), or both.
    ingredients_text: str | None = None
    ingredients: list[str] = field(default_factory=list)
    # Allergen codes the source itself declares present (restaurant allergen records, product
    # database allergen tags). None = the source makes no explicit declaration.
    declared_allergens: list[str] | None = None
    cross_contact: list[CrossContactDecl] = field(default_factory=list)
    # Free-text precautionary statements, e.g. "May contain traces of nuts."
    cross_contact_text: str | None = None
    # Whether the source claims to be the full ingredient list.
    complete: bool = True
    # 0-1; e.g. OCR confidence. Below MIN_SOURCE_CONFIDENCE the source can't support absence.
    confidence: float = 1.0
    url: str | None = None
    note: str = ""


@dataclass
class AnalysisContext:
    # Confidence that the identified product/dish is the right one (None = not applicable).
    identity_confidence: float | None = None
    identity_label: str = ""  # e.g. "Brand X Oat Biscuits 200g" or "Butter Chicken"
    limitations: list[str] = field(default_factory=list)  # pipeline-level, e.g. "Image is blurry"


@dataclass
class AllergyProfile:
    allergen_codes: set[str]
    custom_terms: list[str] = field(default_factory=list)


# ---- Engine output ----


class EvidenceRef(BaseModel):
    source_id: str
    source_name: str
    source_type: SourceType
    # presence | declared | cross_contact | candidate | candidate_cross_contact | possible_source | claim
    kind: str
    ingredient: str = ""
    matched_term: str = ""
    detail: str = ""
    matched_as: str = ""  # the normalized text, when it differs from what was written ("caseln" -> "casein")
    match_method: str = ""  # exact | spelling | ocr_correction | fuzzy | qualified
    concept: str = ""  # ingredient concept slug, when the finding came through the knowledge layer
    relationship_path: str = ""  # e.g. "sodium caseinate, derived from casein, is a milk protein, ..."
    certainty: str = ""  # definite | possible (concept relationships)


class IngredientResolution(BaseModel):
    """How one ingredient item was understood: from the written text to a canonical concept."""

    source_id: str
    original_text: str = ""  # as written
    text: str  # normalized
    # ingredient | compound | component | class_label | qualified | source_qualifier
    role: str = "ingredient"
    parent: str | None = None
    # allergen_linked | possible_allergen | ambiguous_source | no_known_allergen | unresolved | class_name
    status: str
    match_method: str = "exact"  # exact | spelling | ocr_correction | fuzzy | qualified | none
    match_confidence: float = 1.0
    corrections: list[str] = Field(default_factory=list)
    suggestion: str | None = None  # an unaccepted guess for an unresolved item
    concept: str | None = None
    concept_name: str = ""
    foodon_id: str | None = None
    allergens: list[str] = Field(default_factory=list)
    possible_allergens: list[str] = Field(default_factory=list)
    relationship_path: str = ""


class AllergenAssessment(BaseModel):
    code: str  # allergen code, or "custom:<term>" for a custom allergy
    name: str
    is_custom: bool = False
    outcome: Outcome
    outcome_label: str
    reasons: list[str]
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Jurisdictions whose allergen-labelling rules list this allergen (regulatory, not scientific).
    regulated_in: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    kind: str  # allergen_disagreement | ingredient_list_mismatch | declaration_mismatch
    allergen_code: str | None = None
    description: str
    source_ids: list[str]


class SourceSummary(BaseModel):
    id: str
    type: SourceType
    type_label: str
    name: str
    url: str | None = None
    ingredients: list[str]
    complete: bool
    confidence: float
    used_for_absence: bool
    not_used_for_absence_reason: str = ""


class AnalysisResult(BaseModel):
    overall_outcome: Outcome
    overall_label: str
    identity_label: str = ""
    your_allergens: list[AllergenAssessment]
    other_allergens: list[AllergenAssessment]
    sources: list[SourceSummary]
    conflicts: list[Conflict]
    limitations: list[str]
    nonspecific_ingredients: list[str]
    ingredients: list[IngredientResolution] = Field(default_factory=list)
    knowledge_version: int = 0
    explanation: str = ""
    explanation_source: str = "template"  # template | llm
    disclaimer: str = (
        "AllergyGuard reports the evidence it found and what is still unknown. It does not certify "
        "food as safe and is not medical advice. Always check with the manufacturer or restaurant "
        "staff before eating."
    )
