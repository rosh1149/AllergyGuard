import enum

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin


class UserRole(str, enum.Enum):
    customer = "customer"
    restaurant = "restaurant"
    admin = "admin"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    allergies: Mapped[list["CustomerAllergy"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    custom_allergies: Mapped[list["CustomerCustomAllergy"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["RestaurantMembership"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )


class CustomerAllergy(Base):
    """A standard allergen the customer declared at sign-up (or later edited)."""

    __tablename__ = "customer_allergies"
    __table_args__ = (UniqueConstraint("user_id", "allergen_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    allergen_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="RESTRICT"))

    user: Mapped[User] = relationship(back_populates="allergies")
    allergen: Mapped["Allergen"] = relationship()  # noqa: F821


class CustomerCustomAllergy(Base):
    """A free-text allergy that is not one of the standard allergens (e.g. 'kiwi')."""

    __tablename__ = "customer_custom_allergies"
    __table_args__ = (UniqueConstraint("user_id", "term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    term: Mapped[str] = mapped_column(String(100))

    user: Mapped[User] = relationship(back_populates="custom_allergies")
