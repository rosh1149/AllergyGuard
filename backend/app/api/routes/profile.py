from fastapi import APIRouter

from app.api.deps import DB, CurrentCustomer
from app.schemas.auth import AllergyProfileIn, AllergyProfileOut
from app.services.users import allergy_profile_out, set_allergy_profile

router = APIRouter(prefix="/me/allergy-profile", tags=["customer profile"])


@router.get("", response_model=AllergyProfileOut)
def get_profile(user: CurrentCustomer) -> AllergyProfileOut:
    return allergy_profile_out(user)


@router.put("", response_model=AllergyProfileOut)
def update_profile(data: AllergyProfileIn, user: CurrentCustomer, db: DB) -> AllergyProfileOut:
    set_allergy_profile(db, user, data)
    db.commit()
    db.refresh(user)
    return allergy_profile_out(user)
