from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentCustomer
from app.engine.service import AssessmentLink, analyze_for_user
from app.models.common import utcnow
from app.engine.types import AnalysisContext, AnalysisResult
from app.sources.label import LabelTextAdapter

router = APIRouter(prefix="/analyze", tags=["analysis"])


class TextAnalysisIn(BaseModel):
    ingredients_text: str = Field(min_length=2, max_length=10_000)
    may_contain_text: str = Field(default="", max_length=2_000)
    product_name: str = Field(default="", max_length=200)


@router.post("/text", response_model=AnalysisResult)
def analyze_text(data: TextAnalysisIn, user: CurrentCustomer, db: DB) -> AnalysisResult:
    """Check an ingredient list typed in from a label, against the customer's saved allergies."""
    label = LabelTextAdapter().convert(data.ingredients_text, data.may_contain_text, data.product_name, utcnow())
    ctx = AnalysisContext(identity_label=data.product_name)
    return analyze_for_user(db, user, label.sources, ctx, record=AssessmentLink(kind="text"))
