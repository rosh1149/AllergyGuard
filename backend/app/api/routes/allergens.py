from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB
from app.models import Allergen
from app.schemas.auth import AllergenOut

router = APIRouter(prefix="/allergens", tags=["allergens"])


@router.get("", response_model=list[AllergenOut])
def list_allergens(db: DB) -> list[Allergen]:
    """Public list used by the sign-up screen to pick allergies."""
    return list(db.scalars(select(Allergen).order_by(Allergen.name)).all())
