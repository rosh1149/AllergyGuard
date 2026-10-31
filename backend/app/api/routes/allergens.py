from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB
from app.engine.service import get_knowledge_base
from app.models import Allergen
from app.schemas.auth import AllergenOut

router = APIRouter(prefix="/allergens", tags=["allergens"])


@router.get("", response_model=list[AllergenOut])
def list_allergens(db: DB, jurisdiction: str | None = None) -> list[AllergenOut]:
    """Public list used by the sign-up screen. `jurisdiction` (EU, US, IN) limits it to that region's list."""
    kb = get_knowledge_base(db)
    out = []
    for a in db.scalars(select(Allergen).order_by(Allergen.name)).all():
        regions = kb.regulated_in(a.code)
        if jurisdiction and jurisdiction.upper() not in regions:
            continue
        out.append(AllergenOut(id=a.id, code=a.code, name=a.name, description=a.description, regulated_in=regions))
    return out
