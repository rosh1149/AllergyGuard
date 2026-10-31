"""Bring the database schema to the latest Alembic revision.

Three starting points are handled:

* an empty database: every migration runs from the baseline (0001) up;
* a database Alembic already manages: the migrations it has not yet run are applied;
* a database created before Alembic was adopted ("legacy"): the old additive upgrader adds the
  columns it knows about, any baseline table that is missing is created, the database is stamped at
  the baseline revision, and the later migrations then run as usual. Migrations after the baseline
  use `if_not_exists` where they add objects, so they also apply cleanly to an adopted database.

Everything happens in one transaction (PostgreSQL DDL is transactional): a failed migration leaves
the database exactly as it was.

    alembic upgrade head          # the same thing from the command line (run in backend/)
    alembic downgrade -1          # roll back the most recent migration
    alembic upgrade head --sql    # print the SQL for review instead of running it
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.db.base import Base
from app.db.upgrade import upgrade_schema_on

BACKEND = Path(__file__).resolve().parents[2]
BASELINE = "0001"
# The tables the baseline revision creates. Legacy adoption creates only these (when missing); any
# table added later arrives through its own migration.
BASELINE_TABLES = (
    "allergens", "allergen_hierarchy", "allergen_terms", "allergen_records", "assessments", "audit_records", "branches",
    "concept_aliases", "concept_relations", "cross_contact_records", "customer_allergies",
    "customer_custom_allergies", "feedback_suggestions", "ingredient_concepts", "ingredient_records",
    "knowledge_changes", "knowledge_proposals", "knowledge_versions", "manufacturer_sites", "menu_items", "product_snapshots",
    "recipe_versions", "regulatory_entries", "regulatory_lists", "restaurant_memberships", "restaurants",
    "review_items", "unresolved_ingredients", "users",
)


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False  # keep the application's logging setup
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def migrate(engine: Engine) -> str:
    """Upgrade to head. Returns what was done: "created", "upgraded", "adopted" or "current"."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        before = MigrationContext.configure(conn).get_current_revision()
        if before is None and tables & set(BASELINE_TABLES):
            upgrade_schema_on(conn)
            meta = Base.metadata
            Base.metadata.create_all(conn, tables=[meta.tables[t] for t in BASELINE_TABLES if t in meta.tables])
            command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
            return "adopted"
        command.upgrade(cfg, "head")
        if before is None:
            return "created"
        return "current" if before == MigrationContext.configure(conn).get_current_revision() else "upgraded"
