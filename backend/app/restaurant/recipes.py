"""Recipe versions: every change to a dish's ingredient/allergen/cross-contact records creates a new
version. Older versions are kept unchanged, so the history of trusted records can be traced."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.knowledge import KnowledgeBase
from app.engine.types import CrossContactDecl, EvidenceSource, SourceType
from app.models import (
    Allergen,
    AllergenRecord,
    AuditRecord,
    CrossContactRecord,
    IngredientRecord,
    MenuItem,
    RecipeVersion,
    User,
    VerificationStatus,
)
from app.models.common import utcnow
from app.schemas.restaurant import (
    AllergenWarning,
    CrossContactIn,
    CrossContactOut,
    IngredientIn,
    IngredientOut,
    RecipeIn,
    RecipeVersionOut,
)


def audit(
    db: Session,
    restaurant_id: int,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditRecord(
            restaurant_id=restaurant_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def allergen_map(db: Session, codes: set[str]) -> dict[str, Allergen]:
    found = {a.code: a for a in db.scalars(select(Allergen).where(Allergen.code.in_(codes))).all()} if codes else {}
    unknown = codes - set(found)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown allergen codes: {', '.join(sorted(unknown))}"
        )
    return found


def current_version(item: MenuItem) -> RecipeVersion | None:
    return next((v for v in item.recipe_versions if v.is_current), None)


def create_version(db: Session, item: MenuItem, recipe: RecipeIn, actor: User) -> RecipeVersion:
    codes = {c for i in recipe.ingredients for c in i.allergen_codes} | set(recipe.dish_allergen_codes)
    codes |= {c.allergen_code for c in recipe.cross_contacts}
    allergens = allergen_map(db, codes)

    for v in item.recipe_versions:
        v.is_current = False
    db.flush()
    number = (db.scalar(select(func.max(RecipeVersion.version_number)).where(RecipeVersion.menu_item_id == item.id)) or 0) + 1
    version = RecipeVersion(
        menu_item_id=item.id,
        version_number=number,
        is_current=True,
        verification_status=VerificationStatus.verified if recipe.mark_verified else VerificationStatus.unverified,
        change_note=recipe.change_note,
        created_by_id=actor.id,
        verified_by_id=actor.id if recipe.mark_verified else None,
        verified_at=utcnow() if recipe.mark_verified else None,
    )
    db.add(version)
    db.flush()

    for ing in recipe.ingredients:
        rec = IngredientRecord(recipe_version_id=version.id, name=ing.name.strip(), notes=ing.notes)
        db.add(rec)
        db.flush()
        for code in dict.fromkeys(ing.allergen_codes):
            db.add(
                AllergenRecord(
                    recipe_version_id=version.id,
                    allergen_id=allergens[code].id,
                    ingredient_record_id=rec.id,
                    evidence=f"{rec.name} contains {allergens[code].name.lower()}",
                )
            )
    for code in dict.fromkeys(recipe.dish_allergen_codes):
        db.add(
            AllergenRecord(
                recipe_version_id=version.id, allergen_id=allergens[code].id, evidence="Declared for the dish"
            )
        )
    seen: set[str] = set()
    for cc in recipe.cross_contacts:
        if cc.allergen_code in seen:
            continue
        seen.add(cc.allergen_code)
        db.add(
            CrossContactRecord(
                recipe_version_id=version.id,
                allergen_id=allergens[cc.allergen_code].id,
                level=cc.level,
                description=cc.description,
            )
        )
    db.flush()
    db.refresh(version)
    db.refresh(item)
    return version


def snapshot(version: RecipeVersion | None) -> RecipeIn:
    """The version's contents as a RecipeIn, for building the next version from it."""
    if version is None:
        return RecipeIn()
    by_ing: dict[int, list[str]] = {}
    dish_codes: list[str] = []
    for ar in version.allergen_records:
        if ar.ingredient_record_id is None:
            dish_codes.append(ar.allergen.code)
        else:
            by_ing.setdefault(ar.ingredient_record_id, []).append(ar.allergen.code)
    return RecipeIn(
        ingredients=[
            IngredientIn(name=i.name, notes=i.notes, allergen_codes=by_ing.get(i.id, [])) for i in version.ingredients
        ],
        dish_allergen_codes=dish_codes,
        cross_contacts=[
            CrossContactIn(allergen_code=c.allergen.code, level=c.level, description=c.description)
            for c in version.cross_contacts
        ],
    )


def warnings_for(kb: KnowledgeBase, version: RecipeVersion) -> list[AllergenWarning]:
    declared = {ar.allergen.code for ar in version.allergen_records}
    out: list[AllergenWarning] = []
    for ing in version.ingredients:
        for code in sorted({m.allergen_code for m in kb.match(ing.name.lower())} - declared):
            out.append(
                AllergenWarning(
                    ingredient=ing.name,
                    allergen_code=code,
                    allergen_name=kb.name(code),
                    message=f"'{ing.name}' usually indicates {kb.name(code)}, but the recipe doesn't declare it.",
                )
            )
    return out


def _user_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    return u.full_name if u else None


def version_out(db: Session, kb: KnowledgeBase, version: RecipeVersion) -> RecipeVersionOut:
    by_ing: dict[int, list[str]] = {}
    dish_codes: list[str] = []
    for ar in version.allergen_records:
        if ar.ingredient_record_id is None:
            dish_codes.append(ar.allergen.code)
        else:
            by_ing.setdefault(ar.ingredient_record_id, []).append(ar.allergen.code)
    return RecipeVersionOut(
        id=version.id,
        version_number=version.version_number,
        is_current=version.is_current,
        verification_status=version.verification_status,
        change_note=version.change_note,
        created_at=version.created_at,
        created_by=_user_name(db, version.created_by_id),
        verified_by=_user_name(db, version.verified_by_id),
        verified_at=version.verified_at,
        ingredients=[
            IngredientOut(id=i.id, name=i.name, notes=i.notes, allergen_codes=sorted(by_ing.get(i.id, [])))
            for i in version.ingredients
        ],
        dish_allergen_codes=sorted(dish_codes),
        cross_contacts=[
            CrossContactOut(allergen_code=c.allergen.code, level=c.level, description=c.description)
            for c in version.cross_contacts
        ],
        warnings=warnings_for(kb, version),
    )


def to_evidence(item: MenuItem, version: RecipeVersion, restaurant_name: str) -> EvidenceSource:
    verified = version.verification_status == VerificationStatus.verified
    return EvidenceSource(
        id=f"recipe:{version.id}",
        type=SourceType.restaurant_verified if verified else SourceType.restaurant_unverified,
        name=f"{restaurant_name}: {item.name} (recipe v{version.version_number})",
        ingredients=[i.name for i in version.ingredients],
        declared_allergens=sorted({ar.allergen.code for ar in version.allergen_records}),
        cross_contact=[
            CrossContactDecl(c.allergen.code, c.level.value, c.description) for c in version.cross_contacts
        ],
        complete=True,
    )
