"""Index assessments by kind and time, for the administrators' assessment lookup.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_assessments_kind_created_at", "assessments", ["kind", "created_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_assessments_kind_created_at", table_name="assessments", if_exists=True)
