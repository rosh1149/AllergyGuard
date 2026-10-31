from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, CurrentCustomer
from app.models import FeedbackSuggestion
from app.restaurant.feedback import clarify, feedback_out, submit
from app.schemas.restaurant import FeedbackClarificationIn, FeedbackIn, FeedbackOut

router = APIRouter(tags=["feedback (customer)"])


@router.post("/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
def submit_feedback(data: FeedbackIn, user: CurrentCustomer, db: DB) -> FeedbackOut:
    """Suggest a correction to a restaurant dish. It stays unverified until restaurant staff review it."""
    s = submit(db, user, data)
    db.commit()
    return feedback_out(db, s)


@router.get("/me/feedback", response_model=list[FeedbackOut])
def my_feedback(user: CurrentCustomer, db: DB) -> list[FeedbackOut]:
    q = select(FeedbackSuggestion).where(FeedbackSuggestion.customer_id == user.id).order_by(FeedbackSuggestion.created_at.desc())
    return [feedback_out(db, s) for s in db.scalars(q).all()]


@router.post("/me/feedback/{feedback_id}/clarification", response_model=FeedbackOut)
def answer_clarification(feedback_id: int, data: FeedbackClarificationIn, user: CurrentCustomer, db: DB) -> FeedbackOut:
    """Answer the restaurant's question about your suggestion. It then goes back for review."""
    s = db.get(FeedbackSuggestion, feedback_id)
    if s is None or s.customer_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found.")
    clarify(db, user, s, data.message)
    db.commit()
    return feedback_out(db, s)
