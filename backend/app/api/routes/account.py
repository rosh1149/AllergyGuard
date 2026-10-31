"""The customer's own data: export everything held about them, or delete the account."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentCustomer
from app.core.observability import log_event
from app.core.security import verify_password
from app.models import Assessment, FeedbackSuggestion
from app.restaurant.feedback import feedback_out
from app.services.users import allergy_profile_out

router = APIRouter(prefix="/me", tags=["customer account"])


@router.get("/export")
def export_my_data(user: CurrentCustomer, db: DB) -> dict:
    """Everything AllergyGuard stores about you (photos are never stored)."""
    assessments = db.scalars(select(Assessment).where(Assessment.customer_id == user.id)
                             .order_by(Assessment.created_at)).all()
    feedback = db.scalars(select(FeedbackSuggestion).where(FeedbackSuggestion.customer_id == user.id)
                          .order_by(FeedbackSuggestion.created_at)).all()
    return {
        "account": {"email": user.email, "full_name": user.full_name, "created_at": user.created_at},
        "allergy_profile": allergy_profile_out(user).model_dump(),
        "assessments": [{"id": a.id, "kind": a.kind, "label": a.label, "overall_outcome": a.overall_outcome,
                         "outcomes": a.outcomes, "sources": a.sources, "created_at": a.created_at} for a in assessments],
        "feedback": [feedback_out(db, s).model_dump() for s in feedback],
    }


class DeleteAccountIn(BaseModel):
    password: str


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(data: DeleteAccountIn, user: CurrentCustomer, db: DB) -> None:
    """Deletes the account, allergy profile and stored results. Suggestions already sent to
    restaurants are kept for their records, without your name."""
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Incorrect password.")
    db.delete(user)
    db.commit()
    log_event("account.deleted", role="customer")
