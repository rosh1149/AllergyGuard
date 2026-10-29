import re

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Allergen,
    Branch,
    CustomerAllergy,
    CustomerCustomAllergy,
    MembershipRole,
    Restaurant,
    RestaurantMembership,
    User,
    UserRole,
)
from app.schemas.auth import (
    AllergenOut,
    AllergyProfileIn,
    AllergyProfileOut,
    MembershipOut,
    RestaurantRegister,
    UserOut,
)
from app.core.security import hash_password


def email_taken(db: Session, email: str) -> bool:
    return db.scalar(select(User.id).where(User.email == email.lower())) is not None


def set_allergy_profile(db: Session, user: User, profile: AllergyProfileIn) -> None:
    """Replace the customer's allergy profile. Requires at least one allergy."""
    if not profile.allergen_codes and not profile.custom_allergies:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Select at least one allergen or enter a custom allergy.",
        )
    allergens = db.scalars(select(Allergen).where(Allergen.code.in_(profile.allergen_codes))).all()
    unknown = set(profile.allergen_codes) - {a.code for a in allergens}
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown allergen codes: {', '.join(sorted(unknown))}"
        )
    user.allergies.clear()
    user.custom_allergies.clear()
    db.flush()
    user.allergies.extend(CustomerAllergy(allergen_id=a.id) for a in allergens)
    user.custom_allergies.extend(CustomerCustomAllergy(term=t) for t in profile.custom_allergies)


def allergy_profile_out(user: User) -> AllergyProfileOut:
    return AllergyProfileOut(
        allergens=sorted(
            (AllergenOut.model_validate(ca.allergen) for ca in user.allergies), key=lambda a: a.name
        ),
        custom_allergies=sorted(c.term for c in user.custom_allergies),
    )


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        allergy_profile=allergy_profile_out(user) if user.role == UserRole.customer else None,
        memberships=[
            MembershipOut(restaurant_id=m.restaurant_id, restaurant_name=m.restaurant.name, role=m.role)
            for m in user.memberships
        ],
    )


def _unique_slug(db: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "restaurant"
    slug, n = base, 2
    while db.scalar(select(Restaurant.id).where(Restaurant.slug == slug)) is not None:
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_restaurant_owner(db: Session, data: RestaurantRegister) -> User:
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        full_name=data.full_name.strip(),
        role=UserRole.restaurant,
    )
    restaurant = Restaurant(
        name=data.restaurant_name.strip(), slug=_unique_slug(db, data.restaurant_name), cuisine=data.cuisine
    )
    db.add_all([user, restaurant])
    db.flush()
    db.add(RestaurantMembership(user_id=user.id, restaurant_id=restaurant.id, role=MembershipRole.owner))
    db.add(
        Branch(
            restaurant_id=restaurant.id,
            name=data.branch_name.strip(),
            address=data.branch_address,
            city=data.branch_city,
        )
    )
    return user
