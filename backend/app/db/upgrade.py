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

# Values added to existing enum types (Postgres: ALTER TYPE ... ADD VALUE IF NOT EXISTS).
ENUM_VALUES = [
    ("suggestion_type", "incorrect_allergen"),
    ("suggestion_type", "outdated_recipe"),
    ("suggestion_status", "clarification_requested"),
]

COLUMNS = [
    ("recipe_versions", "cross_contact_reviewed", "BOOLEAN NOT NULL DEFAULT false"),
    ("cross_contact_records", "kind", "cross_contact_kind NOT NULL DEFAULT 'other'"),
    ("cross_contact_records", "status", "cross_contact_status NOT NULL DEFAULT 'verified'"),
    ("feedback_suggestions", "clarification_request", "TEXT NOT NULL DEFAULT ''"),
    ("feedback_suggestions", "clarification_response", "TEXT NOT NULL DEFAULT ''"),
    ("feedback_suggestions", "clarified_at", "TIMESTAMP WITH TIME ZONE"),
    ("assessments", "trace", "JSON NOT NULL DEFAULT '[]'"),
    ("assessments", "duration_ms", "INTEGER"),
]


def upgrade_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for name, values in ENUMS.items():
            exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).first()
            if not exists:
                conn.execute(text(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in values)})"))
        for name, value in ENUM_VALUES:
            exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).first()
            if exists:  # a new database gets the full type from create_all
                conn.execute(text(f"ALTER TYPE {name} ADD VALUE IF NOT EXISTS '{value}'"))
        for table, column, ddl in COLUMNS:
            conn.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
