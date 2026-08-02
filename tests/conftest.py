"""Test fixtures: a throwaway SQLite DB and an authenticated API client."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (register tables on metadata)
from app.config import settings
from app.database import Base, get_db
from app.main import app

# Tests always use the in-memory mock price source and parser, regardless of any
# local .env (which may point PRICE_SOURCE at the live Hotellook API).
settings.price_source = "mock"
settings.parser = "mock"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path/'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session, TestingSession
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    _, TestingSession = db_session

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(client):
    """A client with a registered, logged-in user and its Authorization header set."""
    client.post("/auth/register", json={"email": "traveler@example.com", "password": "supersecret1"})
    token = client.post(
        "/auth/login",
        data={"username": "traveler@example.com", "password": "supersecret1"},
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
