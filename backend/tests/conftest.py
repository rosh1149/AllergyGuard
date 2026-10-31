import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://allergyguard:allergyguard@localhost:5432/allergyguard_test"
)
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["BACKGROUND_INLINE"] = "true"
os.environ.setdefault("SOURCE_BACKOFF_SECONDS", "0")  # retries without waiting  # queue work runs before the response, so tests can see it

from sqlalchemy.engine import make_url  # noqa: E402

# The tests wipe the database they run against. Refuse anything that is not clearly a test database,
# so a mistyped TEST_DATABASE_URL can never destroy real data.
_DB_NAME = make_url(os.environ["DATABASE_URL"]).database or ""
if not _DB_NAME.endswith("_test"):
    raise SystemExit(f"Refusing to run tests against database {_DB_NAME!r}: its name must end in '_test'.")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


def reset_database(eng=engine) -> None:
    """Drop every table, type and the Alembic version record: an empty database."""
    assert (eng.url.database or "").endswith("_test"), eng.url.database
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(autouse=True)
def _fresh_circuit_breakers():
    """A source failing in one test must not leave its circuit open for the next."""
    from app.sources.base import reset_breakers
    reset_breakers()
    yield


@pytest.fixture(scope="session")
def client():
    reset_database()
    with TestClient(app) as c:  # lifespan runs init_db (migrations + seed)
        yield c
    reset_database()
