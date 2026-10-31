from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import MembershipRole, RestaurantMembership, User, UserRole

DB = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        payload = decode_access_token(creds.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_customer(user: CurrentUser) -> User:
    if user.role != UserRole.customer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Customer account required.")
    return user


CurrentCustomer = Annotated[User, Depends(require_customer)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Knowledge administrator account required.")
    return user


CurrentAdmin = Annotated[User, Depends(require_admin)]


class RestaurantAccess:
    """Dependency enforcing that the current user belongs to the restaurant in the URL path.

    This is the tenant boundary: every restaurant-dashboard route depends on it, and every query in
    those routes filters by `membership.restaurant_id`.
    """

    def __init__(self, *allowed: MembershipRole):
        self.allowed = set(allowed) or set(MembershipRole)

    def __call__(
        self, db: DB, user: CurrentUser, restaurant_id: Annotated[int, Path()]
    ) -> RestaurantMembership:
        membership = db.scalar(
            select(RestaurantMembership).where(
                RestaurantMembership.user_id == user.id,
                RestaurantMembership.restaurant_id == restaurant_id,
            )
        )
        # Same 404 for "doesn't exist" and "not yours" so tenants can't probe each other's ids.
        if membership is None or not membership.restaurant.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found.")
        if membership.role not in self.allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not allow this action.")
        return membership


# Role groups used by dashboard routes (Part 4).
ANY_MEMBER = RestaurantAccess()
EDITORS = RestaurantAccess(MembershipRole.owner, MembershipRole.manager)
OWNERS = RestaurantAccess(MembershipRole.owner)
