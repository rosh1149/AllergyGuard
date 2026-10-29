"""Idempotent seeding of reference data (allergen knowledge base) and optional demo data."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import (
    Allergen,
    AllergenRecord,
    AllergenTerm,
    AllergenTermKind,
    Branch,
    CrossContactLevel,
    CrossContactRecord,
    CustomerAllergy,
    IngredientRecord,
    MembershipRole,
    MenuItem,
    RecipeVersion,
    Restaurant,
    RestaurantMembership,
    User,
    UserRole,
    VerificationStatus,
)
from app.seed.allergen_data import ALLERGENS

log = logging.getLogger(__name__)

DEMO_PASSWORD = "Demo1234!"
DEMO_OWNER_EMAIL = "owner@demo.allergyguard.app"
DEMO_CUSTOMER_EMAIL = "customer@demo.allergyguard.app"


def seed_allergens(db: Session) -> None:
    existing = {a.code: a for a in db.scalars(select(Allergen)).all()}
    for spec in ALLERGENS:
        allergen = existing.get(spec["code"])
        if allergen is None:
            allergen = Allergen(code=spec["code"], name=spec["name"], description=spec["description"])
            db.add(allergen)
            db.flush()
        else:
            allergen.name = spec["name"]
            allergen.description = spec["description"]

        have = {(t.term, t.kind) for t in allergen.terms}
        for term in dict.fromkeys(spec["synonyms"]):
            key = (term.lower(), AllergenTermKind.synonym)
            if key not in have:
                allergen.terms.append(AllergenTerm(term=key[0], kind=key[1]))
                have.add(key)
        for term, note in spec["exclusions"]:
            key = (term.lower(), AllergenTermKind.exclusion)
            if key not in have:
                allergen.terms.append(AllergenTerm(term=key[0], kind=key[1], note=note))
                have.add(key)
    db.commit()


def _recipe(
    db: Session,
    allergens: dict[str, Allergen],
    item: MenuItem,
    ingredients: list[tuple[str, list[str]]],
    cross_contacts: list[tuple[str, CrossContactLevel, str]],
    owner_id: int,
) -> None:
    version = RecipeVersion(
        menu_item_id=item.id,
        version_number=1,
        is_current=True,
        verification_status=VerificationStatus.verified,
        change_note="Initial recipe",
        created_by_id=owner_id,
        verified_by_id=owner_id,
    )
    db.add(version)
    db.flush()
    for name, allergen_codes in ingredients:
        ing = IngredientRecord(recipe_version_id=version.id, name=name)
        db.add(ing)
        db.flush()
        for code in allergen_codes:
            db.add(
                AllergenRecord(
                    recipe_version_id=version.id,
                    allergen_id=allergens[code].id,
                    ingredient_record_id=ing.id,
                    evidence=f"{name} contains {allergens[code].name.lower()}",
                )
            )
    for code, level, desc in cross_contacts:
        db.add(
            CrossContactRecord(
                recipe_version_id=version.id, allergen_id=allergens[code].id, level=level, description=desc
            )
        )


def seed_demo(db: Session) -> None:
    if db.scalar(select(User).where(User.email == DEMO_OWNER_EMAIL)):
        return
    allergens = {a.code: a for a in db.scalars(select(Allergen)).all()}

    owner = User(
        email=DEMO_OWNER_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name="Demo Owner",
        role=UserRole.restaurant,
    )
    customer = User(
        email=DEMO_CUSTOMER_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name="Demo Customer",
        role=UserRole.customer,
    )
    db.add_all([owner, customer])
    db.flush()
    for code in ("milk", "peanuts"):
        db.add(CustomerAllergy(user_id=customer.id, allergen_id=allergens[code].id))

    restaurant = Restaurant(name="Spice Route Kitchen", slug="spice-route-kitchen", cuisine="Indian")
    db.add(restaurant)
    db.flush()
    db.add(RestaurantMembership(user_id=owner.id, restaurant_id=restaurant.id, role=MembershipRole.owner))
    branch = Branch(restaurant_id=restaurant.id, name="Downtown", address="12 Market Street", city="Springfield")
    db.add(branch)
    db.flush()

    dishes = [
        (
            "Butter Chicken",
            "Main",
            [
                ("chicken", []),
                ("butter", ["milk"]),
                ("cream", ["milk"]),
                ("tomato", []),
                ("cashew paste", ["tree_nuts"]),
                ("garam masala", []),
            ],
            [("peanuts", CrossContactLevel.possible, "Shared kitchen; peanuts used in other dishes.")],
        ),
        (
            "Chana Masala",
            "Main",
            [("chickpeas", []), ("onion", []), ("tomato", []), ("vegetable oil", []), ("spices", [])],
            [],
        ),
        (
            "Vegetable Samosa",
            "Starter",
            [("wheat flour", ["gluten"]), ("potato", []), ("peas", []), ("mustard seeds", ["mustard"])],
            [("crustaceans", CrossContactLevel.known, "Fried in the same oil as prawn pakora.")],
        ),
    ]
    for name, category, ingredients, cross in dishes:
        item = MenuItem(restaurant_id=restaurant.id, branch_id=branch.id, name=name, category=category)
        db.add(item)
        db.flush()
        _recipe(db, allergens, item, ingredients, cross, owner.id)
    db.commit()
    log.info("Seeded demo data (owner=%s, customer=%s)", DEMO_OWNER_EMAIL, DEMO_CUSTOMER_EMAIL)
