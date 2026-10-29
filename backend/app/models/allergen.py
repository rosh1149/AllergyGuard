import enum

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AllergenTermKind(str, enum.Enum):
    # The term indicates the allergen (e.g. "whey" -> milk).
    synonym = "synonym"
    # The term looks related but does NOT indicate the allergen (e.g. "nutmeg" is not a tree nut).
    exclusion = "exclusion"


class Allergen(Base):
    """Allergen knowledge base: one row per allergen category."""

    __tablename__ = "allergens"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    is_major: Mapped[bool] = mapped_column(Boolean, default=True)

    terms: Mapped[list["AllergenTerm"]] = relationship(
        back_populates="allergen", cascade="all, delete-orphan"
    )


class AllergenTerm(Base):
    """Ingredient term mapped to an allergen, stored in normalized (lower-case) form."""

    __tablename__ = "allergen_terms"
    __table_args__ = (UniqueConstraint("allergen_id", "term", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    allergen_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="CASCADE"), index=True)
    term: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[AllergenTermKind] = mapped_column(Enum(AllergenTermKind, name="allergen_term_kind"))
    note: Mapped[str] = mapped_column(Text, default="")

    allergen: Mapped[Allergen] = relationship(back_populates="terms")
