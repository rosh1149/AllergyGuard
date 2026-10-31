from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import (
    CrossContactKind,
    CrossContactLevel,
    CrossContactStatus,
    MembershipRole,
    MenuItemStatus,
    SuggestionStatus,
    SuggestionType,
    VerificationStatus,
)


class ORM(BaseModel):
    model_config = {"from_attributes": True}


# ---- restaurant / branches / team ----


class RestaurantOut(ORM):
    id: int
    name: str
    slug: str
    cuisine: str
    description: str
    is_active: bool
    settings: dict


class RestaurantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    cuisine: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    settings: dict | None = None


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class BranchOut(ORM):
    id: int
    name: str
    address: str
    city: str
    is_active: bool


class MemberIn(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    role: MembershipRole
    # Required only when the email has no account yet; the new member signs in with it.
    initial_password: str | None = Field(default=None, min_length=8, max_length=72)


class MemberUpdate(BaseModel):
    role: MembershipRole


class MemberOut(BaseModel):
    user_id: int
    email: str
    full_name: str
    role: MembershipRole


# ---- menu / recipes ----


class IngredientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=1000)
    # Allergens this ingredient contains, as declared by staff.
    allergen_codes: list[str] = Field(default_factory=list)


class CrossContactIn(BaseModel):
    allergen_code: str
    level: CrossContactLevel = CrossContactLevel.possible
    description: str = Field(default="", max_length=1000)
    kind: CrossContactKind = CrossContactKind.other
    status: CrossContactStatus = CrossContactStatus.verified
    ingredient: str = Field(default="", max_length=200)  # "" = the whole dish


class RecipeIn(BaseModel):
    ingredients: list[IngredientIn] = Field(default_factory=list, max_length=200)
    # Allergens present in the dish that aren't tied to one ingredient.
    dish_allergen_codes: list[str] = Field(default_factory=list)
    cross_contacts: list[CrossContactIn] = Field(default_factory=list)
    change_note: str = Field(default="", max_length=1000)
    # Staff assessed cross-contact for this recipe (so "no cross-contact records" means none found).
    cross_contact_reviewed: bool = False
    # Editors can confirm the recipe in the same step; otherwise it stays unverified.
    mark_verified: bool = False

    @field_validator("ingredients")
    @classmethod
    def _unique(cls, v: list[IngredientIn]) -> list[IngredientIn]:
        names = [i.name.strip().lower() for i in v]
        if len(names) != len(set(names)):
            raise ValueError("Ingredient names must be unique within a recipe.")
        return v


class MenuItemIn(BaseModel):
    branch_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=100)
    status: MenuItemStatus = MenuItemStatus.available
    recipe: RecipeIn = Field(default_factory=RecipeIn)


class MenuItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    status: MenuItemStatus | None = None


class IngredientOut(BaseModel):
    id: int
    name: str
    notes: str
    allergen_codes: list[str]


class CrossContactOut(BaseModel):
    allergen_code: str
    level: CrossContactLevel
    description: str
    kind: CrossContactKind = CrossContactKind.other
    status: CrossContactStatus = CrossContactStatus.verified
    ingredient: str = Field(default="", max_length=200)  # "" = the whole dish


class VersionChanges(BaseModel):
    """What changed compared with the previous version."""

    previous_version: int | None = None
    ingredients_added: list[str] = Field(default_factory=list)
    ingredients_removed: list[str] = Field(default_factory=list)
    allergens_added: list[str] = Field(default_factory=list)
    allergens_removed: list[str] = Field(default_factory=list)
    cross_contact_added: list[str] = Field(default_factory=list)
    cross_contact_removed: list[str] = Field(default_factory=list)


class AllergenWarning(BaseModel):
    ingredient: str
    allergen_code: str
    allergen_name: str
    message: str


class RecipeVersionOut(BaseModel):
    id: int
    version_number: int
    is_current: bool
    verification_status: VerificationStatus
    change_note: str
    created_at: datetime
    created_by: str | None
    verified_by: str | None
    verified_at: datetime | None
    ingredients: list[IngredientOut]
    dish_allergen_codes: list[str]
    cross_contacts: list[CrossContactOut]
    cross_contact_reviewed: bool = False
    changes: VersionChanges | None = None
    # Ingredients whose name indicates an allergen that the recipe doesn't declare.
    warnings: list[AllergenWarning] = Field(default_factory=list)


class MenuItemOut(BaseModel):
    id: int
    branch_id: int
    branch_name: str
    name: str
    description: str
    category: str
    status: MenuItemStatus
    current_version: RecipeVersionOut | None
    pending_feedback: int = 0


class MenuItemDetail(MenuItemOut):
    versions: list[RecipeVersionOut]


class IngredientCheckIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=200)


class IngredientCheckOut(BaseModel):
    name: str
    allergen_codes: list[str]
    allergen_names: list[str]


# ---- feedback ----


class FeedbackIn(BaseModel):
    menu_item_id: int
    suggestion_type: SuggestionType
    ingredient_name: str = Field(default="", max_length=200)
    allergen_code: str | None = None
    message: str = Field(default="", max_length=2000)


class FeedbackImpact(BaseModel):
    resulting_version_number: int
    version_is_current: bool
    changes: VersionChanges | None = None
    assessments_since: int  # customer checks that used the corrected recipe version
    assessments_flagging_allergen: int | None = None  # of those, how many reported the allergen


class FeedbackEvent(BaseModel):
    action: str  # submitted | clarification_requested | clarified | confirmed | rejected
    at: datetime
    note: str = ""


class FeedbackClarificationIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class FeedbackOut(BaseModel):
    id: int
    restaurant_id: int
    branch_id: int
    menu_item_id: int
    menu_item_name: str
    recipe_version_number: int | None
    suggestion_type: SuggestionType
    ingredient_name: str
    allergen_code: str | None
    message: str
    status: SuggestionStatus
    created_at: datetime
    decided_at: datetime | None
    decision_note: str
    resulting_version_number: int | None
    clarification_request: str = ""
    clarification_response: str = ""
    clarified_at: datetime | None = None
    impact: FeedbackImpact | None = None
    history: list[FeedbackEvent] = Field(default_factory=list)


class FeedbackDecisionIn(BaseModel):
    decision: SuggestionStatus  # confirmed | rejected | clarification_requested (note = the question)
    note: str = Field(default="", max_length=2000)

    @field_validator("decision")
    @classmethod
    def _decided(cls, v: SuggestionStatus) -> SuggestionStatus:
        if v == SuggestionStatus.unverified:
            raise ValueError("Decision must be 'confirmed', 'rejected' or 'clarification_requested'.")
        return v


# ---- audit / dashboard ----


class AuditOut(ORM):
    id: int
    actor_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    details: dict
    created_at: datetime


class DashboardOut(BaseModel):
    restaurant: RestaurantOut
    your_role: MembershipRole
    branches: list[BranchOut]
    menu_items: int
    unverified_recipes: int
    pending_feedback: int
    team_members: int
