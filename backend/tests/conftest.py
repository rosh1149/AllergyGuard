import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://allergyguard:allergyguard@localhost:5432/allergyguard_test"
)
os.environ["SEED_DEMO_DATA"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:  # lifespan runs init_db (tables + seed)
        yield c
    Base.metadata.drop_all(bind=engine)
