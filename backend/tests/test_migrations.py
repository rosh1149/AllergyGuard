"""Alembic migrations: a new database, an upgrade, a rollback, and adoption of a pre-Alembic database.

Runs against a separate scratch database (<test db>_mig_test) so the shared test database used by
the API tests is never touched."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db.base import Base
from app.db.migrate import BASELINE, alembic_config, current_revision, head_revision, migrate
from app.db.upgrade import COLUMNS
from tests.conftest import reset_database

_base = make_url(get_settings().database_url)
MIG_URL = _base.set(database=_base.database.removesuffix("_test") + "_mig_test")


@pytest.fixture(scope="module")
def mig_engine():
    admin = create_engine(_base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                {"n": MIG_URL.database}).first():
                conn.execute(text(f'CREATE DATABASE "{MIG_URL.database}"'))
    except Exception as exc:  # no CREATEDB privilege
        pytest.skip(f"cannot create the scratch migration database: {exc}")
    finally:
        admin.dispose()
    eng = create_engine(MIG_URL)
    reset_database(eng)
    yield eng
    reset_database(eng)
    eng.dispose()


def _cfg(eng):
    cfg = alembic_config(eng.url.render_as_string(hide_password=False))
    return cfg


def _diff(eng):
    with eng.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)


def test_new_database_matches_the_models(mig_engine):
    reset_database(mig_engine)
    assert migrate(mig_engine) == "created"
    assert current_revision(mig_engine) == head_revision()
    assert _diff(mig_engine) == []  # the migrations build exactly the schema the models describe
    assert migrate(mig_engine) == "current"  # running again changes nothing


def test_every_migration_rolls_back_and_forward(mig_engine):
    reset_database(mig_engine)
    cfg = _cfg(mig_engine)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    assert current_revision(mig_engine) != head_revision()
    assert "ix_assessments_kind_created_at" not in {
        i["name"] for i in inspect(mig_engine).get_indexes("assessments")}
    command.downgrade(cfg, "base")  # all the way down: nothing of ours is left
    with mig_engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        types = conn.execute(text("SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                                  "WHERE n.nspname = 'public' AND t.typtype = 'e'")).scalars().all()
        assert types == []
    command.upgrade(cfg, "head")
    assert _diff(mig_engine) == []


def test_failed_migration_leaves_the_database_unchanged(mig_engine, monkeypatch):
    reset_database(mig_engine)
    cfg = _cfg(mig_engine)
    command.upgrade(cfg, BASELINE)
    import alembic.command as ac

    def boom(*a, **k):
        raise RuntimeError("simulated failure")

    real = ac.upgrade

    def failing_upgrade(cfg, rev, **k):
        real(cfg, rev, **k)  # the migration runs inside migrate()'s transaction ...
        boom()               # ... and then something fails before it commits

    monkeypatch.setattr("app.db.migrate.command.upgrade", failing_upgrade)
    with pytest.raises(RuntimeError):
        migrate(mig_engine)
    assert current_revision(mig_engine) == BASELINE  # rolled back as a whole


def test_pre_alembic_database_is_adopted(mig_engine):
    """A database made by create_all before Alembic, missing the newer columns and one table."""
    reset_database(mig_engine)
    Base.metadata.create_all(mig_engine)
    with mig_engine.begin() as conn:
        for table, column, _ in COLUMNS:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        conn.execute(text("DROP TABLE manufacturer_sites"))
        conn.execute(text("DROP INDEX ix_assessments_kind_created_at"))
        conn.execute(text("INSERT INTO allergens (code, name, description, is_major) "
                          "VALUES ('legacy', 'Legacy', '', false)"))
    assert current_revision(mig_engine) is None
    assert migrate(mig_engine) == "adopted"
    assert current_revision(mig_engine) == head_revision()
    assert _diff(mig_engine) == []
    with mig_engine.connect() as conn:  # existing data kept
        assert conn.execute(text("SELECT count(*) FROM allergens WHERE code = 'legacy'")).scalar() == 1


def test_the_test_suite_refuses_a_non_test_database():
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "TEST_DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/allergyguard"}
    r = subprocess.run([sys.executable, "-c", "import tests.conftest"], capture_output=True, text=True,
                       env=env, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode != 0
    assert "must end in '_test'" in r.stderr
