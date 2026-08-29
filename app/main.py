"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine
from .routers import auth, billing, inbound, jobs

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


# The marketing site (myroomwatch.com, Vercel) calls this API from the browser —
# signup and the dashboard — so it needs an explicit CORS allowance. Origins are
# configured, never "*": credentials-bearing requests must come from known hosts.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(billing.router)
app.include_router(inbound.router)
