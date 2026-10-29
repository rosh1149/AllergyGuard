"""Public restaurant directory for customers: names, branches and menu item names only.
Recipe and allergen records stay private and are only used inside an analysis."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.models import Branch, MenuItem, MenuItemStatus, Restaurant

router = APIRouter(prefix="/public/restaurants", tags=["restaurants (customer)"])


class PublicBranch(BaseModel):
    id: int
    name: str
    city: str
    address: str


class PublicRestaurant(BaseModel):
    id: int
    name: str
    cuisine: str
    branches: list[PublicBranch]


class PublicMenuItem(BaseModel):
    id: int
    name: str
    category: str
    description: str


@router.get("", response_model=list[PublicRestaurant])
def search_restaurants(
    _: CurrentUser, db: DB, q: str = "", limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[PublicRestaurant]:
    stmt = select(Restaurant).where(Restaurant.is_active.is_(True))
    if q.strip():
        stmt = stmt.where(Restaurant.name.ilike(f"%{q.strip()}%"))
    out = []
    for r in db.scalars(stmt.order_by(Restaurant.name).limit(limit)).all():
        branches = [
            PublicBranch(id=b.id, name=b.name, city=b.city, address=b.address) for b in r.branches if b.is_active
        ]
        if branches:
            out.append(PublicRestaurant(id=r.id, name=r.name, cuisine=r.cuisine, branches=branches))
    return out


@router.get("/{restaurant_id}/branches/{branch_id}/menu", response_model=list[PublicMenuItem])
def branch_menu(restaurant_id: int, branch_id: int, _: CurrentUser, db: DB) -> list[PublicMenuItem]:
    branch = db.get(Branch, branch_id)
    if branch is None or branch.restaurant_id != restaurant_id or not branch.is_active or not branch.restaurant.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found.")
    items = db.scalars(
        select(MenuItem)
        .where(MenuItem.branch_id == branch_id, MenuItem.status == MenuItemStatus.available)
        .order_by(MenuItem.category, MenuItem.name)
    ).all()
    return [PublicMenuItem(id=i.id, name=i.name, category=i.category, description=i.description) for i in items]
