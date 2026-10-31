"""Legacy additive upgrades, kept only to adopt databases created before Alembic was introduced.

Each entry adds one column (and its enum type) if it is missing, bringing such a database to the
Alembic baseline revision (0001) before it is stamped; see app.db.migrate. This list is frozen: new
schema changes are Alembic migrations in backend/migrations/versions/.
"""

from sqlalchemy import Connection, Engine, text

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
    ("relation_type", "contains_protein"),
    ("relation_type", "is_regulated_as"),
    ("proposal_kind", "foodon_mapping"),
]

COLUMNS = [
    ("recipe_versions", "cross_contact_reviewed", "BOOLEAN NOT NULL DEFAULT false"),
    ("cross_contact_records", "kind", "cross_contact_kind NOT NULL DEFAULT 'other'"),
    ("cross_contact_records", "status", "cross_contact_status NOT NULL DEFAULT 'verified'"),
    ("cross_contact_records", "ingredient", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("recipe_versions", "effective_from", "TIMESTAMP WITH TIME ZONE"),
    ("feedback_suggestions", "clarification_request", "TEXT NOT NULL DEFAULT ''"),
    ("feedback_suggestions", "clarification_response", "TEXT NOT NULL DEFAULT ''"),
    ("feedback_suggestions", "clarified_at", "TIMESTAMP WITH TIME ZONE"),
    ("assessments", "trace", "JSON NOT NULL DEFAULT '[]'"),
    ("assessments", "duration_ms", "INTEGER"),
    ("ingredient_concepts", "concept_type", "VARCHAR(30) NOT NULL DEFAULT 'ingredient'"),
    ("unresolved_ingredients", "candidates", "JSON NOT NULL DEFAULT '[]'"),
    ("unresolved_ingredients", "reasons", "JSON NOT NULL DEFAULT '[]'"),
]


def upgrade_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        upgrade_schema_on(conn)


def upgrade_schema_on(conn: Connection) -> None:
    for name, values in ENUMS.items():
        exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).first()
        if not exists:
            conn.execute(text(f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v) for v in values)})"))
    for name, value in ENUM_VALUES:
        exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}).first()
        if exists:  # a type the database does not have yet is created whole later
            conn.execute(text(f"ALTER TYPE {name} ADD VALUE IF NOT EXISTS '{value}'"))
    for table, column, ddl in COLUMNS:
        conn.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
