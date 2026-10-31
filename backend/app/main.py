import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import (
    allergens,
    analyze,
    auth,
    feedback,
    history,
    knowledge,
    packaged,
    profile,
    public,
    restaurant_analysis,
    restaurants,
    review,
)
from app.core.config import get_settings
from app.db.init_db import init_db
from app.db.session import engine

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api"
app.include_router(auth.router, prefix=API)
app.include_router(profile.router, prefix=API)
app.include_router(allergens.router, prefix=API)
app.include_router(restaurants.router, prefix=API)
app.include_router(analyze.router, prefix=API)
app.include_router(packaged.router, prefix=API)
app.include_router(restaurant_analysis.router, prefix=API)
app.include_router(public.router, prefix=API)
app.include_router(feedback.router, prefix=API)
app.include_router(knowledge.router, prefix=API)
app.include_router(history.router, prefix=API)
app.include_router(review.admin_router, prefix=API)
app.include_router(review.restaurant_router, prefix=API)
app.include_router(review.sources_router, prefix=API)


@app.get(f"{API}/health", tags=["health"])
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
