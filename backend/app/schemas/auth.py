from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import MembershipRole, UserRole


def _check_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes.")
    return v


def _clean_terms(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        t = " ".join(v.strip().lower().split())
        if t:
            seen[t] = None
    return list(seen)


class AllergyProfileIn(BaseModel):
    """The customer's allergies: standard allergen codes plus optional free-text extras."""

    allergen_codes: list[str] = Field(default_factory=list)
    custom_allergies: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allergen_codes")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return _clean_terms(v)

    @field_validator("custom_allergies")
    @classmethod
    def _custom(cls, v: list[str]) -> list[str]:
        cleaned = _clean_terms(v)
        if any(len(t) > 100 for t in cleaned):
            raise ValueError("Each custom allergy must be at most 100 characters.")
        return cleaned


class CustomerRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(min_length=1, max_length=200)
    allergy_profile: AllergyProfileIn

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


class RestaurantRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(min_length=1, max_length=200)
    restaurant_name: str = Field(min_length=1, max_length=200)
    cuisine: str = Field(default="", max_length=100)
    branch_name: str = Field(default="Main branch", min_length=1, max_length=200)
    branch_address: str = Field(default="", max_length=300)
    branch_city: str = Field(default="", max_length=100)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _check_password(v)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class AllergenOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    code: str
    name: str
    description: str
    # Jurisdictions whose allergen-labelling rules list this allergen (EU, US, IN).
    regulated_in: list[str] = []


class AllergyProfileOut(BaseModel):
    allergens: list[AllergenOut]
    custom_allergies: list[str]


class MembershipOut(BaseModel):
    restaurant_id: int
    restaurant_name: str
    role: MembershipRole


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: UserRole
    allergy_profile: AllergyProfileOut | None = None
    memberships: list[MembershipOut] = Field(default_factory=list)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
