from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import ANY_MEMBER
from app.models import MembershipRole, RestaurantMembership

router = APIRouter(prefix="/restaurants/{restaurant_id}", tags=["restaurant dashboard"])


class BranchSummary(BaseModel):
    id: int
    name: str
    city: str


class RestaurantSummary(BaseModel):
    id: int
    name: str
    slug: str
    cuisine: str
    your_role: MembershipRole
    branches: list[BranchSummary]


@router.get("", response_model=RestaurantSummary)
def get_restaurant(membership: Annotated[RestaurantMembership, Depends(ANY_MEMBER)]) -> RestaurantSummary:
    """Dashboard home. Full dashboard management routes are added in Part 4."""
    r = membership.restaurant
    return RestaurantSummary(
        id=r.id,
        name=r.name,
        slug=r.slug,
        cuisine=r.cuisine,
        your_role=membership.role,
        branches=[BranchSummary(id=b.id, name=b.name, city=b.city) for b in r.branches],
    )
