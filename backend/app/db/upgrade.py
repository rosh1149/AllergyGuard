"""Additive schema upgrades for databases created by earlier versions.

`create_all` creates missing tables but never changes existing ones. Each entry here adds one column
(and its enum type) if it is missing. Only additive, idempotent changes belong here; anything that
rewrites or drops data needs a real migration tool (Alembic) before production use.
"""

from sqlalchemy import Engine, text

ENUMS = {
    "cross_contact_kind": ["shared_equipment", "shared_fryer", "preparation_area", "utensils", "storage",
                           "manufacturing_facility", "supplier", "other"],
    "cross_contact_status": ["verified", "reported", "unknown"],
}

COLUMNS = [
    ("recipe_versions", "cross_contact_reviewed", "BOOLEAN NOT NULL DEFAULT false"),
    ("cross_contact_records", "kind", "cross_contact_kind NOT NULL DEFAULT 'other'"),
    ("cross_contact_records", "status", "cross_contact_status NOT NULL DEFAULT 'verified'"),
]


def upgrade_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for name, values in ENUMS.items():
            exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).first()
            if not exists:
                conn.execute(text(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in values)})"))
        for table, column, ddl in COLUMNS:
            conn.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
