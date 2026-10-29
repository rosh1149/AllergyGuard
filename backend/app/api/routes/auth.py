from fastapi import APIRouter, HTTPException, status

from app.api.deps import DB, CurrentUser
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, UserRole
from app.schemas.auth import CustomerRegister, LoginIn, RestaurantRegister, TokenOut, UserOut
from app.services.users import (
    create_restaurant_owner,
    email_taken,
    set_allergy_profile,
    user_out,
)
from sqlalchemy import select

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(user: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(user.id, user.role.value), user=user_out(user))


@router.post("/register/customer", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register_customer(data: CustomerRegister, db: DB) -> TokenOut:
    """Customer sign-up. The allergy profile is required here and is used for every later analysis."""
    if email_taken(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        full_name=data.full_name.strip(),
        role=UserRole.customer,
    )
    db.add(user)
    db.flush()
    set_allergy_profile(db, user, data.allergy_profile)
    db.commit()
    db.refresh(user)
    return _token(user)


@router.post("/register/restaurant", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register_restaurant(data: RestaurantRegister, db: DB) -> TokenOut:
    """Restaurant sign-up: creates the owner account, the restaurant and its first branch."""
    if email_taken(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    user = create_restaurant_owner(db, data)
    db.commit()
    db.refresh(user)
    return _token(user)


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, db: DB) -> TokenOut:
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or not user.is_active or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    return _token(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user)
