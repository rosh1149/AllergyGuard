from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DB, CurrentCustomer
from app.models import FeedbackSuggestion
from app.restaurant.feedback import feedback_out, submit
from app.schemas.restaurant import FeedbackIn, FeedbackOut

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
