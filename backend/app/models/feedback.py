import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, utcnow


class SuggestionType(str, enum.Enum):
    missing_ingredient = "missing_ingredient"
    incorrect_ingredient = "incorrect_ingredient"
    missing_allergen = "missing_allergen"
    cross_contact = "cross_contact"
    other = "other"


class SuggestionStatus(str, enum.Enum):
    unverified = "unverified"  # default; trusted records are unchanged
    confirmed = "confirmed"
    rejected = "rejected"


class FeedbackSuggestion(TimestampMixin, Base):
    __tablename__ = "feedback_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    recipe_version_id: Mapped[int | None] = mapped_column(ForeignKey("recipe_versions.id", ondelete="SET NULL"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    suggestion_type: Mapped[SuggestionType] = mapped_column(Enum(SuggestionType, name="suggestion_type"))
    ingredient_name: Mapped[str] = mapped_column(String(200), default="")
    allergen_id: Mapped[int | None] = mapped_column(ForeignKey("allergens.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, name="suggestion_status"), default=SuggestionStatus.unverified, index=True
    )
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    resulting_recipe_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="SET NULL")
    )

    allergen: Mapped["Allergen | None"] = relationship()  # noqa: F821


class AuditRecord(Base):
    """Append-only log of decisions and changes to trusted restaurant records."""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[int | None] = mapped_column()
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
