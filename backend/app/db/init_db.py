from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.seed.seed import seed_allergens, seed_demo

import app.models  # noqa: F401  (register all models on Base.metadata)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_allergens(db)
        if get_settings().seed_demo_data:
            seed_demo(db)
