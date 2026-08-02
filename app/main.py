"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .database import Base, engine
from .routers import auth, billing, jobs

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Convenience for local dev: auto-create tables on SQLite. In production use
    # Alembic migrations (`alembic upgrade head`) against Postgres instead.
    if settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Hotel price-drop rebooking — watch a refundable booking, alert on "
    "like-for-like drops, guard the cancellation deadline.",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(billing.router)
