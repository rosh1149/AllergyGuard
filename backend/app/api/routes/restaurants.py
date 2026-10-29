"""Restaurant dashboard API (private). Every route is scoped to a restaurant the caller belongs to;
every query filters by that restaurant id, and ids from other restaurants return 404."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ANY_MEMBER, DB, EDITORS, OWNERS
from app.core.security import hash_password
from app.engine.service import get_knowledge_base
from app.models import (
    AuditRecord,
    Branch,
    FeedbackSuggestion,
    MembershipRole,
    MenuItem,
    MenuItemStatus,
    RecipeVersion,
    RestaurantMembership,
    SuggestionStatus,
    User,
    UserRole,
    VerificationStatus,
)
from app.models.common import utcnow
from app.restaurant import feedback as fb
from app.restaurant.recipes import audit, create_version, current_version, version_out
from app.schemas.restaurant import (
    AuditOut,
    BranchIn,
    BranchOut,
    BranchUpdate,
    DashboardOut,
    FeedbackDecisionIn,
    FeedbackOut,
    IngredientCheckIn,
    IngredientCheckOut,
    MemberIn,
    MemberOut,
    MemberUpdate,
    MenuItemDetail,
    MenuItemIn,
    MenuItemOut,
    MenuItemUpdate,
    RecipeIn,
    RecipeVersionOut,
    RestaurantOut,
    RestaurantUpdate,
)

router = APIRouter(prefix="/restaurants/{restaurant_id}", tags=["restaurant dashboard"])

Member = Annotated[RestaurantMembership, Depends(ANY_MEMBER)]
Editor = Annotated[RestaurantMembership, Depends(EDITORS)]
Owner = Annotated[RestaurantMembership, Depends(OWNERS)]


def _404(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found.")


def _branch(db: Session, m: RestaurantMembership, branch_id: int) -> Branch:
    b = db.get(Branch, branch_id)
    if b is None or b.restaurant_id != m.restaurant_id:
        raise _404("Branch")
    return b


def _item(db: Session, m: RestaurantMembership, item_id: int) -> MenuItem:
    item = db.get(MenuItem, item_id)
    if item is None or item.restaurant_id != m.restaurant_id:
        raise _404("Menu item")
    return item


def _pending(db: Session, item_id: int) -> int:
    return db.scalar(
        select(func.count()).where(
            FeedbackSuggestion.menu_item_id == item_id, FeedbackSuggestion.status == SuggestionStatus.unverified
        )
    ) or 0


def _item_out(db: Session, item: MenuItem, detail: bool = False) -> MenuItemOut | MenuItemDetail:
    kb = get_knowledge_base(db)
    cur = current_version(item)
    data = dict(
        id=item.id,
        branch_id=item.branch_id,
        branch_name=item.branch.name,
        name=item.name,
        description=item.description,
        category=item.category,
        status=item.status,
        current_version=version_out(db, kb, cur) if cur else None,
        pending_feedback=_pending(db, item.id),
    )
    if detail:
        versions = sorted(item.recipe_versions, key=lambda v: -v.version_number)
        return MenuItemDetail(**data, versions=[version_out(db, kb, v) for v in versions])
    return MenuItemOut(**data)


# ---- overview & settings ----


@router.get("", response_model=DashboardOut)
def dashboard(m: Member, db: DB) -> DashboardOut:
    rid = m.restaurant_id
    item_ids = select(MenuItem.id).where(MenuItem.restaurant_id == rid, MenuItem.status != MenuItemStatus.archived)
    return DashboardOut(
        restaurant=RestaurantOut.model_validate(m.restaurant),
        your_role=m.role,
        branches=[BranchOut.model_validate(b) for b in sorted(m.restaurant.branches, key=lambda b: b.id)],
        menu_items=db.scalar(select(func.count()).select_from(item_ids.subquery())) or 0,
        unverified_recipes=db.scalar(
            select(func.count()).where(
                RecipeVersion.menu_item_id.in_(item_ids),
                RecipeVersion.is_current.is_(True),
                RecipeVersion.verification_status == VerificationStatus.unverified,
            )
        ) or 0,
        pending_feedback=db.scalar(
            select(func.count()).where(
                FeedbackSuggestion.restaurant_id == rid, FeedbackSuggestion.status == SuggestionStatus.unverified
            )
        ) or 0,
        team_members=len(m.restaurant.memberships),
    )


@router.patch("", response_model=RestaurantOut)
def update_restaurant(data: RestaurantUpdate, m: Owner, db: DB) -> RestaurantOut:
    r = m.restaurant
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(r, k, v)
    audit(db, r.id, m.user_id, "restaurant.updated", "restaurant", r.id, {"fields": sorted(changes)})
    db.commit()
    return RestaurantOut.model_validate(r)


# ---- branches ----


@router.get("/branches", response_model=list[BranchOut])
def list_branches(m: Member) -> list[Branch]:
    return sorted(m.restaurant.branches, key=lambda b: b.id)


@router.post("/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(data: BranchIn, m: Owner, db: DB) -> Branch:
    b = Branch(restaurant_id=m.restaurant_id, **data.model_dump())
    db.add(b)
    db.flush()
    audit(db, m.restaurant_id, m.user_id, "branch.created", "branch", b.id, data.model_dump())
    db.commit()
    return b


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: int, data: BranchUpdate, m: Owner, db: DB) -> Branch:
    b = _branch(db, m, branch_id)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(b, k, v)
    audit(db, m.restaurant_id, m.user_id, "branch.updated", "branch", b.id, changes)
    db.commit()
    return b


# ---- team ----


def _member_out(mm: RestaurantMembership) -> MemberOut:
    return MemberOut(user_id=mm.user_id, email=mm.user.email, full_name=mm.user.full_name, role=mm.role)


def _owners(m: RestaurantMembership) -> int:
    return sum(1 for x in m.restaurant.memberships if x.role == MembershipRole.owner)


def _membership(m: RestaurantMembership, user_id: int) -> RestaurantMembership:
    target = next((x for x in m.restaurant.memberships if x.user_id == user_id), None)
    if target is None:
        raise _404("Team member")
    return target


@router.get("/team", response_model=list[MemberOut])
def list_team(m: Member) -> list[MemberOut]:
    return [_member_out(x) for x in sorted(m.restaurant.memberships, key=lambda x: x.id)]


@router.post("/team", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(data: MemberIn, m: Owner, db: DB) -> MemberOut:
    email = data.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        if not data.initial_password:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "This email has no account yet; set an initial password."
            )
        user = User(
            email=email,
            password_hash=hash_password(data.initial_password),
            full_name=data.full_name.strip() or email.split("@")[0],
            role=UserRole.restaurant,
        )
        db.add(user)
        db.flush()
    elif user.role != UserRole.restaurant:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email belongs to a customer account.")
    if any(x.user_id == user.id for x in m.restaurant.memberships):
        raise HTTPException(status.HTTP_409_CONFLICT, "This person is already a team member.")
    mm = RestaurantMembership(user_id=user.id, restaurant_id=m.restaurant_id, role=data.role)
    db.add(mm)
    db.flush()
    audit(db, m.restaurant_id, m.user_id, "team.added", "user", user.id, {"role": data.role.value})
    db.commit()
    db.refresh(mm)
    return _member_out(mm)


@router.patch("/team/{user_id}", response_model=MemberOut)
def update_member(user_id: int, data: MemberUpdate, m: Owner, db: DB) -> MemberOut:
    target = _membership(m, user_id)
    if target.role == MembershipRole.owner and data.role != MembershipRole.owner and _owners(m) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A restaurant must keep at least one owner.")
    old = target.role
    target.role = data.role
    audit(db, m.restaurant_id, m.user_id, "team.role_changed", "user", user_id, {"from": old.value, "to": data.role.value})
    db.commit()
    return _member_out(target)


@router.delete("/team/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(user_id: int, m: Owner, db: DB) -> None:
    target = _membership(m, user_id)
    if target.role == MembershipRole.owner and _owners(m) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A restaurant must keep at least one owner.")
    db.delete(target)
    audit(db, m.restaurant_id, m.user_id, "team.removed", "user", user_id, {"role": target.role.value})
    db.commit()


# ---- menu & recipes ----


@router.get("/menu", response_model=list[MenuItemOut])
def list_menu(
    m: Member,
    db: DB,
    branch_id: int | None = None,
    item_status: Annotated[MenuItemStatus | None, Query(alias="status")] = None,
) -> list[MenuItemOut]:
    q = select(MenuItem).where(MenuItem.restaurant_id == m.restaurant_id)
    if branch_id is not None:
        q = q.where(MenuItem.branch_id == branch_id)
    q = q.where(MenuItem.status == item_status) if item_status else q.where(MenuItem.status != MenuItemStatus.archived)
    return [_item_out(db, i) for i in db.scalars(q.order_by(MenuItem.category, MenuItem.name)).all()]


@router.post("/menu", response_model=MenuItemDetail, status_code=status.HTTP_201_CREATED)
def create_menu_item(data: MenuItemIn, m: Editor, db: DB) -> MenuItemDetail:
    branch = _branch(db, m, data.branch_id)
    item = MenuItem(
        restaurant_id=m.restaurant_id,
        branch_id=branch.id,
        name=data.name.strip(),
        description=data.description,
        category=data.category,
        status=data.status,
    )
    db.add(item)
    db.flush()
    version = create_version(db, item, data.recipe, m.user)
    audit(
        db, m.restaurant_id, m.user_id, "menu_item.created", "menu_item", item.id,
        {"name": item.name, "recipe_version": version.version_number},
    )
    db.commit()
    return _item_out(db, item, detail=True)


@router.get("/menu/{item_id}", response_model=MenuItemDetail)
def get_menu_item(item_id: int, m: Member, db: DB) -> MenuItemDetail:
    return _item_out(db, _item(db, m, item_id), detail=True)


@router.patch("/menu/{item_id}", response_model=MenuItemDetail)
def update_menu_item(item_id: int, data: MenuItemUpdate, m: Editor, db: DB) -> MenuItemDetail:
    item = _item(db, m, item_id)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(item, k, v)
    audit(db, m.restaurant_id, m.user_id, "menu_item.updated", "menu_item", item.id, {k: str(v) for k, v in changes.items()})
    db.commit()
    return _item_out(db, item, detail=True)


@router.delete("/menu/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_menu_item(item_id: int, m: Editor, db: DB) -> None:
    """Archives the item (recipe history is kept)."""
    item = _item(db, m, item_id)
    item.status = MenuItemStatus.archived
    audit(db, m.restaurant_id, m.user_id, "menu_item.archived", "menu_item", item.id)
    db.commit()


@router.post("/menu/{item_id}/versions", response_model=MenuItemDetail, status_code=status.HTTP_201_CREATED)
def create_recipe_version(item_id: int, data: RecipeIn, m: Editor, db: DB) -> MenuItemDetail:
    item = _item(db, m, item_id)
    version = create_version(db, item, data, m.user)
    audit(
        db, m.restaurant_id, m.user_id, "recipe.version_created", "recipe_version", version.id,
        {"menu_item_id": item.id, "version": version.version_number, "verified": data.mark_verified,
         "note": data.change_note},
    )
    db.commit()
    return _item_out(db, item, detail=True)


@router.post("/menu/{item_id}/versions/{version_id}/verify", response_model=RecipeVersionOut)
def verify_recipe_version(item_id: int, version_id: int, m: Editor, db: DB) -> RecipeVersionOut:
    item = _item(db, m, item_id)
    version = next((v for v in item.recipe_versions if v.id == version_id), None)
    if version is None:
        raise _404("Recipe version")
    if not version.is_current:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only the current recipe version can be verified.")
    if version.verification_status != VerificationStatus.verified:
        version.verification_status = VerificationStatus.verified
        version.verified_by_id = m.user_id
        version.verified_at = utcnow()
        audit(
            db, m.restaurant_id, m.user_id, "recipe.verified", "recipe_version", version.id,
            {"menu_item_id": item.id, "version": version.version_number},
        )
        db.commit()
    return version_out(db, get_knowledge_base(db), version)


@router.post("/ingredients/check", response_model=list[IngredientCheckOut])
def check_ingredients(data: IngredientCheckIn, m: Member, db: DB) -> list[IngredientCheckOut]:
    """Suggest allergen codes for ingredient names (helps staff fill in allergen records)."""
    kb = get_knowledge_base(db)
    out = []
    for name in data.names:
        codes = sorted({x.allergen_code for x in kb.match(name.lower())})
        out.append(IngredientCheckOut(name=name, allergen_codes=codes, allergen_names=[kb.name(c) for c in codes]))
    return out


# ---- feedback review ----


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    m: Member,
    db: DB,
    feedback_status: Annotated[SuggestionStatus | None, Query(alias="status")] = None,
) -> list[FeedbackOut]:
    q = select(FeedbackSuggestion).where(FeedbackSuggestion.restaurant_id == m.restaurant_id)
    if feedback_status:
        q = q.where(FeedbackSuggestion.status == feedback_status)
    return [fb.feedback_out(db, s) for s in db.scalars(q.order_by(FeedbackSuggestion.created_at.desc())).all()]


@router.post("/feedback/{feedback_id}/decision", response_model=FeedbackOut)
def decide_feedback(feedback_id: int, data: FeedbackDecisionIn, m: Editor, db: DB) -> FeedbackOut:
    s = db.get(FeedbackSuggestion, feedback_id)
    if s is None or s.restaurant_id != m.restaurant_id:
        raise _404("Suggestion")
    fb.decide(db, get_knowledge_base(db), m.restaurant_id, m.user, s, data.decision, data.note)
    db.commit()
    return fb.feedback_out(db, s)


# ---- audit ----


@router.get("/audit", response_model=list[AuditOut])
def audit_log(m: Editor, db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[AuditRecord]:
    q = (
        select(AuditRecord)
        .where(AuditRecord.restaurant_id == m.restaurant_id)
        .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
        .limit(limit)
    )
    return list(db.scalars(q).all())
