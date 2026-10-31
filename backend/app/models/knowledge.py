"""Ingredient knowledge layer: concepts, their relationships to each other and to allergens, regional
allergen lists, and a versioned, audited history of every change to that knowledge."""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, utcnow


class KnowledgeStatus(str, enum.Enum):
    curated = "curated"  # seeded by the project team from regulations and common ingredient knowledge
    verified = "verified"  # approved by a knowledge reviewer through the proposal workflow
    unverified = "unverified"  # recorded but not checked (e.g. a source that could not be consulted)
    retired = "retired"  # no longer used; kept for history


# Statuses the analysis engine uses.
ACTIVE_STATUSES = (KnowledgeStatus.curated, KnowledgeStatus.verified, KnowledgeStatus.unverified)


class RelationType(str, enum.Enum):
    derived_from = "derived_from"
    is_source_of = "is_source_of"
    is_a = "is_a"
    contains_component = "contains_component"
    associated_with_allergen = "associated_with_allergen"


class Certainty(str, enum.Enum):
    definite = "definite"
    possible = "possible"


class KnowledgeVersion(Base):
    """One row per published state of the knowledge base. Assessments record the version they used."""

    __tablename__ = "knowledge_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngredientConcept(TimestampMixin, Base):
    __tablename__ = "ingredient_concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # stable internal id
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Source not stated by the name alone (e.g. "vegetable oil", "lecithin").
    ambiguous_source: Mapped[bool] = mapped_column(default=False)
    foodon_id: Mapped[str | None] = mapped_column(String(40))
    foodon_label: Mapped[str | None] = mapped_column(String(200))
    foodon_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus, name="knowledge_status"))
    source: Mapped[str] = mapped_column(Text, default="")
    added_in_version: Mapped[int] = mapped_column(Integer, default=1)

    aliases: Mapped[list["ConceptAlias"]] = relationship(back_populates="concept", cascade="all, delete-orphan")


class ConceptAlias(Base):
    __tablename__ = "concept_aliases"
    __table_args__ = (UniqueConstraint("concept_id", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("ingredient_concepts.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(200), index=True)  # normalized (lower-case)
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus, name="knowledge_status"))
    source: Mapped[str] = mapped_column(Text, default="")
    added_in_version: Mapped[int] = mapped_column(Integer, default=1)

    concept: Mapped[IngredientConcept] = relationship(back_populates="aliases")


class ConceptRelation(Base):
    """subject --relation--> object (a concept), or subject --associated_with_allergen--> allergen."""

    __tablename__ = "concept_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("ingredient_concepts.id", ondelete="CASCADE"), index=True)
    relation: Mapped[RelationType] = mapped_column(Enum(RelationType, name="relation_type"))
    object_concept_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_concepts.id", ondelete="CASCADE"))
    object_allergen_id: Mapped[int | None] = mapped_column(ForeignKey("allergens.id", ondelete="CASCADE"))
    certainty: Mapped[Certainty] = mapped_column(Enum(Certainty, name="relation_certainty"))
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus, name="knowledge_status"))
    source: Mapped[str] = mapped_column(Text, default="")
    added_in_version: Mapped[int] = mapped_column(Integer, default=1)

    subject: Mapped[IngredientConcept] = relationship(foreign_keys=[subject_id])
    object_concept: Mapped[IngredientConcept | None] = relationship(foreign_keys=[object_concept_id])
    object_allergen: Mapped["Allergen | None"] = relationship()  # noqa: F821


class AllergenParent(Base):
    """Allergen hierarchy: child (e.g. wheat) belongs to parent (cereals containing gluten)."""

    __tablename__ = "allergen_hierarchy"
    __table_args__ = (UniqueConstraint("child_id", "parent_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="CASCADE"))
    parent_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="CASCADE"))
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus, name="knowledge_status"))

    child: Mapped["Allergen"] = relationship(foreign_keys=[child_id])  # noqa: F821
    parent: Mapped["Allergen"] = relationship(foreign_keys=[parent_id])  # noqa: F821


class RegulatoryList(Base):
    __tablename__ = "regulatory_lists"
    __table_args__ = (UniqueConstraint("jurisdiction", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(10), index=True)  # "EU", "US", "IN"
    name: Mapped[str] = mapped_column(String(200))
    legal_reference: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(80))
    status: Mapped[KnowledgeStatus] = mapped_column(Enum(KnowledgeStatus, name="knowledge_status"))
    note: Mapped[str] = mapped_column(Text, default="")

    entries: Mapped[list["RegulatoryEntry"]] = relationship(back_populates="list", cascade="all, delete-orphan")


class RegulatoryEntry(Base):
    __tablename__ = "regulatory_entries"
    __table_args__ = (UniqueConstraint("list_id", "allergen_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("regulatory_lists.id", ondelete="CASCADE"), index=True)
    allergen_id: Mapped[int] = mapped_column(ForeignKey("allergens.id", ondelete="CASCADE"))
    label_name: Mapped[str] = mapped_column(Text)

    list: Mapped[RegulatoryList] = relationship(back_populates="entries")
    allergen: Mapped["Allergen"] = relationship()  # noqa: F821


class ProposalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ProposalKind(str, enum.Enum):
    concept = "concept"
    alias = "alias"
    relation = "relation"
    allergen_term = "allergen_term"
    regulatory_entry = "regulatory_entry"
    retire_relation = "retire_relation"


class KnowledgeProposal(TimestampMixin, Base):
    """A requested change to the knowledge base. Nothing changes until a reviewer approves it."""

    __tablename__ = "knowledge_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ProposalKind] = mapped_column(Enum(ProposalKind, name="proposal_kind"))
    payload: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, name="proposal_status"), default=ProposalStatus.pending, index=True
    )
    proposed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    resulting_version: Mapped[int | None] = mapped_column(Integer)


class KnowledgeChange(Base):
    """Append-only history of knowledge changes, each tied to the version it produced."""

    __tablename__ = "knowledge_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[int | None] = mapped_column()
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_proposals.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UnresolvedStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"  # added to the knowledge base
    ignored = "ignored"  # not an ingredient (OCR noise, marketing text)


class UnresolvedIngredient(Base):
    """Ingredient text the knowledge base could not identify, queued for manual review.

    Stores only the text and where it was seen (source type), never who submitted it."""

    __tablename__ = "unresolved_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    source_types: Mapped[list] = mapped_column(JSON, default=list)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[UnresolvedStatus] = mapped_column(
        Enum(UnresolvedStatus, name="unresolved_status"), default=UnresolvedStatus.open, index=True
    )
    resolved_concept_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_concepts.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, default="")
