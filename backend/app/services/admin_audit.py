"""Audit records for platform-level (administrator) actions: restaurant_id is empty."""

from sqlalchemy.orm import Session

from app.models import AuditRecord, User


def admin_audit(db: Session, actor: User | None, action: str, entity_type: str, entity_id: int | None,
                details: dict | None = None) -> None:
    db.add(AuditRecord(restaurant_id=None, actor_id=actor.id if actor else None, action=action,
                       entity_type=entity_type, entity_id=entity_id, details=details or {}))
    db.commit()
