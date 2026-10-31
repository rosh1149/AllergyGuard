from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

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
    web_page = "web_page"  # a page found on the web, not an approved manufacturer site (candidate only)


@dataclass(frozen=True)
class SourcePolicy:
    """How one kind of source may be used. The engine's decisions are derived from this table."""

    shows_present: bool  # its ingredients/declarations can make an allergen PRESENT
    supports_absence: bool  # when complete, current and readable it can support NOT LISTED
    depends_on_identification: bool  # it describes *an* item: only trusted if identification is confident
    freshness: str  # how age is judged
    role: str  # one line for documentation and the decision-matrix endpoint


SOURCE_POLICY: dict[SourceType, SourcePolicy] = {
    SourceType.label_ocr: SourcePolicy(True, True, False, "photographed now",
                                       "The customer's own label photo: trusted if read reliably."),
    SourceType.label_manual: SourcePolicy(True, True, False, "typed now",
                                          "Label text typed by the customer: trusted, typos are a stated limitation."),
    SourceType.product_database: SourcePolicy(True, True, True, "last update within the product-record limit",
                                              "Product database entry: trusted only for a confidently identified product."),
    SourceType.manufacturer: SourcePolicy(True, True, True, "as dated by the manufacturer",
                                          "Manufacturer information: trusted only for a confidently identified product."),
    SourceType.restaurant_verified: SourcePolicy(True, True, True, "verified within the restaurant-record limit",
                                                 "Staff-verified recipe version: the restaurant's authoritative record."),
    SourceType.restaurant_unverified: SourcePolicy(True, False, True, "not used for absence",
                                                   "Recipe not yet verified by staff: shows allergens, never rules them out."),
    SourceType.public_recipe: SourcePolicy(False, False, False, "not applicable",
                                           "How a dish is commonly made, not this restaurant's recipe: candidates only."),
    SourceType.customer_suggestion: SourcePolicy(False, False, True, "only reports on the current recipe version",
                                                 "Unreviewed customer report: candidates only until staff confirm it."),
    SourceType.web_page: SourcePolicy(False, False, True, "as retrieved",
                                      "A web page that is not an approved manufacturer site: candidates only, never "
                                      "verified just because it is online."),
}

# Derived sets used by the engine (kept as names for readability).
AUTHORITATIVE = {t for t, p in SOURCE_POLICY.items() if p.shows_present}
ABSENCE_CAPABLE = {t for t, p in SOURCE_POLICY.items() if p.supports_absence}
# Label text comes straight from the customer's photo, so it doesn't depend on identification.
IDENTITY_DEPENDENT = {t for t, p in SOURCE_POLICY.items() if p.depends_on_identification and p.shows_present}

SOURCE_TYPE_LABELS = {
    SourceType.label_ocr: "Package label (read from photo)",
    SourceType.label_manual: "Package label (typed by you)",
    SourceType.product_database: "Product database",
    SourceType.manufacturer: "Manufacturer",
    SourceType.restaurant_verified: "Restaurant records (verified)",
    SourceType.restaurant_unverified: "Restaurant records (not yet verified)",
    SourceType.public_recipe: "Public recipe (unverified, not this restaurant)",
    SourceType.customer_suggestion: "Customer suggestion (unverified)",
    SourceType.web_page: "Web page (unverified)",
}

MIN_IDENTITY_CONFIDENCE = 0.7
MIN_SOURCE_CONFIDENCE = 0.6


# ---- Engine inputs (built by pipelines) ----


@dataclass
class CrossContactDecl:
    allergen_code: str
    level: str = "possible"  # "known" | "possible"
    description: str = ""
    # shared_equipment | shared_fryer | preparation_area | utensils | storage | manufacturing_facility |
    # supplier | precautionary_label | other
    kind: str = "other"
    status: str = "verified"  # verified | reported | unknown
    ingredient: str = ""  # the ingredient the risk comes through ("" = the whole item)


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
    # Provenance: what exactly the evidence describes ("barcode 5000159407236", "recipe v3"), how it is
    # verified (label | verified | unverified | community | public | customer) and when it was last
    # known to be correct (verified, updated or photographed). None = unknown.
    reference: str = ""
    verification: str = ""
    as_of: datetime | None = None
    # Days after which the source is too old to rule allergens out (None = no limit).
    max_age_days: int | None = None
    # Whether cross-contact was assessed by the source. None = not applicable / not stated.
    cross_contact_assessed: bool | None = None


class StageRecord(BaseModel):
    """One processing stage of an analysis, for debugging and monitoring (no personal data)."""

    name: str  # barcode | ocr | product_lookup | dish_match | normalization | concept_mapping | fusion | decision ...
    status: str = "done"  # done | skipped | failed
    duration_ms: int | None = None
    counts: dict[str, int | float | str] = Field(default_factory=dict)


@dataclass
class AnalysisContext:
    # Confidence that the identified product/dish is the right one (None = not applicable).
    identity_confidence: float | None = None
    identity_label: str = ""  # e.g. "Brand X Oat Biscuits 200g" or "Butter Chicken"
    limitations: list[str] = field(default_factory=list)  # pipeline-level, e.g. "Image is blurry"
    now: datetime | None = None  # evaluation time (defaults to the current time)
    stages: list[StageRecord] = field(default_factory=list)  # pipeline stages before the engine
    started: float | None = None  # time.perf_counter() when the request started, for total latency


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
    path_nodes: list[str] = Field(default_factory=list)  # concept slugs along the relationship path
    foodon_id: str | None = None  # of the matched concept, when it has one
    allergen_category: str = ""  # the regulated allergen category the finding maps to
    match_confidence: float | None = None
    # Provenance copied from the source, so each finding stands on its own.
    source_reference: str = ""
    source_verification: str = ""
    source_as_of: datetime | None = None
    collected_at: datetime | None = None
    conflict_ids: list[str] = Field(default_factory=list)


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
    candidates: list[str] = Field(default_factory=list)  # close matches considered but not accepted
    unresolved_reasons: list[str] = Field(default_factory=list)  # why the text could not be identified
    interpretations: list[str] = Field(default_factory=list)  # meanings of a name with several meanings
    incomplete: str = ""  # why this item's component list looks incomplete
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
    # What the outcome rests on: direct_ingredient | derived_ingredient | declared_by_source |
    # cross_contact | possible_source | unverified_source | free_from_claim |
    # not_found_in_verified_records | insufficient_evidence
    basis: list[str] = Field(default_factory=list)
    # The decision rules that produced the outcome (see app/engine/rules.py), for auditing.
    rules: list[str] = Field(default_factory=list)
    # Handling-related risk, kept apart from ingredient evidence: reported | none_reported (a trusted
    # source assessed cross-contact and recorded none) | not_assessed (nobody said).
    cross_contact: str = "not_assessed"


class Conflict(BaseModel):
    id: str = ""
    # allergen_disagreement | ingredient_list_mismatch | ingredient_difference | declaration_mismatch |
    # cross_contact_disagreement
    kind: str
    allergen_code: str | None = None
    description: str
    source_ids: list[str]
    resolution: str = ""  # how the engine resolved it (the cautious finding is always kept)
    # cautious_finding_kept | absence_blocked | no_allergen_effect | cross_contact_kept
    handling: str = ""
    status: str = "resolved"  # resolved | unresolved (an unresolved conflict makes the result uncertain)
    only_in: dict[str, list[str]] = Field(default_factory=dict)  # source id -> ingredients only that source lists


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
    reference: str = ""
    verification: str = ""
    as_of: datetime | None = None
    age_days: int | None = None
    fresh: bool = True
    completeness: str = "complete"  # complete | partial | unavailable | conflicting
    unresolved_items: list[str] = Field(default_factory=list)


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
    assessment_id: int | None = None  # the stored record of this assessment (set by the service)
    evaluated_at: datetime | None = None
    # Processing stages with timings and counts (pipeline + engine). Contains no personal data.
    trace: list[StageRecord] = Field(default_factory=list)
    duration_ms: int | None = None
    explanation: str = ""
    explanation_source: str = "template"  # template | llm
    disclaimer: str = (
        "AllergyGuard reports the evidence it found and what is still unknown. It does not certify "
        "food as safe and is not medical advice. Always check with the manufacturer or restaurant "
        "staff before eating."
    )
