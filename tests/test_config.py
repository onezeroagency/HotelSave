"""Settings behaviour that production depends on."""

from app.config import Settings


def _url(database_url: str) -> str:
    return Settings(database_url=database_url).sqlalchemy_url


def test_heroku_style_postgres_scheme_gets_an_explicit_driver():
    """Managed hosts issue postgres:// — SQLAlchemy 2 refuses the scheme
    outright (deploy failure, Aug 2026)."""
    assert (
        _url("postgres://u:p@host:5432/db") == "postgresql+psycopg2://u:p@host:5432/db"
    )


def test_bare_postgresql_scheme_gets_an_explicit_driver():
    """A bare postgresql:// leaves the driver to SQLAlchemy's default, which
    changed under us: 2.0 → psycopg2, 2.1 → psycopg v3 (not installed). The
    first deploy after SQLAlchemy 2.1.9 landed died in pre-deploy with
    ModuleNotFoundError: psycopg (Nov 2026). Explicit driver = immune."""
    assert (
        _url("postgresql://u:p@host:5432/db")
        == "postgresql+psycopg2://u:p@host:5432/db"
    )


def test_already_explicit_driver_passes_through_untouched():
    assert (
        _url("postgresql+psycopg2://u:p@host/db") == "postgresql+psycopg2://u:p@host/db"
    )


def test_sqlite_passes_through_untouched():
    assert _url("sqlite:///./dev.db") == "sqlite:///./dev.db"
