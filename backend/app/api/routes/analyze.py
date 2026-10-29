from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentCustomer
from app.engine.service import analyze_for_user
from app.engine.types import AnalysisContext, AnalysisResult, EvidenceSource, SourceType

router = APIRouter(prefix="/analyze", tags=["analysis"])


class TextAnalysisIn(BaseModel):
    ingredients_text: str = Field(min_length=2, max_length=10_000)
    may_contain_text: str = Field(default="", max_length=2_000)
    product_name: str = Field(default="", max_length=200)


@router.post("/text", response_model=AnalysisResult)
def analyze_text(data: TextAnalysisIn, user: CurrentCustomer, db: DB) -> AnalysisResult:
    """Check an ingredient list typed in from a label, against the customer's saved allergies."""
    source = EvidenceSource(
        id="manual",
        type=SourceType.label_manual,
        name=data.product_name or "Ingredient list you entered",
        ingredients_text=data.ingredients_text,
        cross_contact_text=data.may_contain_text or None,
    )
    ctx = AnalysisContext(identity_label=data.product_name)
    return analyze_for_user(db, user, [source], ctx)
