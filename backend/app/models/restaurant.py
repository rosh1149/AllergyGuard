import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin


class MembershipRole(str, enum.Enum):
    owner = "owner"  # everything, incl. team members and settings
    manager = "manager"  # edit menu/recipe/allergen records, review feedback
    staff = "staff"  # read-only access to the dashboard


class MenuItemStatus(str, enum.Enum):
    available = "available"
    unavailable = "unavailable"
    archived = "archived"


class VerificationStatus(str, enum.Enum):
    verified = "verified"  # confirmed by authorized restaurant staff
    unverified = "unverified"  # entered but not yet confirmed


class CrossContactLevel(str, enum.Enum):
    known = "known"  # e.g. shared fryer that is always used with shellfish
    possible = "possible"  # e.g. shared kitchen surfaces


class CrossContactKind(str, enum.Enum):
    shared_equipment = "shared_equipment"
    shared_fryer = "shared_fryer"
    preparation_area = "preparation_area"
    utensils = "utensils"
    storage = "storage"
    manufacturing_facility = "manufacturing_facility"
    supplier = "supplier"
    other = "other"


class CrossContactStatus(str, enum.Enum):
    verified = "verified"  # checked by restaurant staff
    reported = "reported"  # reported (e.g. by a supplier or a customer) and accepted, not checked in the kitchen
    unknown = "unknown"  # recorded as a concern whose extent is not known


class Restaurant(TimestampMixin, Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    cuisine: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    branches: Mapped[list["Branch"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")
    memberships: Mapped[list["RestaurantMembership"]] = relationship(
        back_populates="restaurant", cascade="all, delete-orphan"
    )


class RestaurantMembership(TimestampMixin, Base):
    __tablename__ = "restaurant_memberships"
    __table_args__ = (UniqueConstraint("user_id", "restaurant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    role: Mapped[MembershipRole] = mapped_column(Enum(MembershipRole, name="membership_role"))

    user: Mapped["User"] = relationship(back_populates="memberships")  # noqa: F821
    restaurant: Mapped[Restaurant] = relationship(back_populates="memberships")


class Branch(TimestampMixin, Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    restaurant: Mapped[Restaurant] = relationship(back_populates="branches")
    menu_items: Mapped[list["MenuItem"]] = relationship(back_populates="branch", cascade="all, delete-orphan")


class MenuItem(TimestampMixin, Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # restaurant_id is denormalized from the branch so every tenant query can filter on it directly.
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[MenuItemStatus] = mapped_column(
        Enum(MenuItemStatus, name="menu_item_status"), default=MenuItemStatus.available
    )

    branch: Mapped[Branch] = relationship(back_populates="menu_items")
    recipe_versions: Mapped[list["RecipeVersion"]] = relationship(
        back_populates="menu_item", cascade="all, delete-orphan", order_by="RecipeVersion.version_number"
    )


class RecipeVersion(TimestampMixin, Base):
    """An immutable snapshot of a dish's ingredients/allergens/cross-contact.

    Changes create a new version; exactly one version per menu item is current.
    """

    __tablename__ = "recipe_versions"
    __table_args__ = (UniqueConstraint("menu_item_id", "version_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"), default=VerificationStatus.unverified
    )
    change_note: Mapped[str] = mapped_column(Text, default="")
    # Staff confirmed they assessed cross-contact for this version (so "no records" means "none found",
    # not "never looked"). Never a safety claim.
    cross_contact_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    menu_item: Mapped[MenuItem] = relationship(back_populates="recipe_versions")
    ingredients: Mapped[list["IngredientRecord"]] = relationship(
        back_populates="recipe_version", cascade="all, delete-orphan"
    )
    allergen_records: Mapped[list["AllergenRecord"]] = relationship(
        back_populates="recipe_version", cascade="all, delete-orphan"
    )
    cross_contacts: Mapped[list["CrossContactRecord"]] = relationship(
        back_populates="recipe_version", cascade="all, delete-orphan"
    )


class IngredientRecord(Base):
    __tablename__ = "ingredient_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")

    recipe_version: Mapped[RecipeVersion] = relationship(back_populates="ingredients")


class AllergenRecord(Base):
    """Staff-declared presence of an allergen in a recipe version, optionally tied to an ingredient."""

    __tablename__ = "allergen_records"
    __table_args__ = (UniqueConstraint("recipe_version_id", "allergen_id", "ingredient_record_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="CASCADE"), index=True
    )
    allergen_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="RESTRICT"))
    ingredient_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingredient_records.id", ondelete="CASCADE")
    )
    evidence: Mapped[str] = mapped_column(Text, default="")

    recipe_version: Mapped[RecipeVersion] = relationship(back_populates="allergen_records")
    allergen: Mapped["Allergen"] = relationship()  # noqa: F821
    ingredient: Mapped[IngredientRecord | None] = relationship()


class CrossContactRecord(Base):
    """Cross-contact risk, kept separate from ingredient presence."""

    __tablename__ = "cross_contact_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_version_id: Mapped[int] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="CASCADE"), index=True
    )
    allergen_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="RESTRICT"))
    level: Mapped[CrossContactLevel] = mapped_column(Enum(CrossContactLevel, name="cross_contact_level"))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[CrossContactKind] = mapped_column(
        Enum(CrossContactKind, name="cross_contact_kind"), default=CrossContactKind.other, server_default="other")
    status: Mapped[CrossContactStatus] = mapped_column(
        Enum(CrossContactStatus, name="cross_contact_status"), default=CrossContactStatus.verified, server_default="verified")

    recipe_version: Mapped[RecipeVersion] = relationship(back_populates="cross_contacts")
    allergen: Mapped["Allergen"] = relationship()  # noqa: F821
