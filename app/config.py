"""Application settings, loaded from environment / .env (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "HotelSave"
    environment: str = "development"

    database_url: str = "sqlite:///./hotelsave.db"

    # Auth
    secret_key: str = "dev-insecure-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7
    algorithm: str = "HS256"

    # Price source (§9)
    price_source: str = "mock"  # mock | hotellook
    drop_floor_abs: float = 10.0
    drop_floor_pct: float = 0.03
    deadline_alert_hours: int = 48

    # Travelpayouts / Hotellook (§9) — DEFUNCT (Hotellook shut down 20 Oct 2025).
    # Kept only for the reference source; see docs/price-source-migration.md.
    travelpayouts_token: str | None = None
    travelpayouts_marker: str | None = None
    travelpayouts_customer_ip: str | None = None  # optional; region-prices the search

    # Booking.com Demand API (§9) — affiliate replacement for Hotellook. Needs a
    # DIRECT Managed Affiliate Partner account (not CJ/Awin) → API key + affiliate id.
    # See docs/price-source-migration.md. Not live yet (pending partner access).
    booking_api_key: str | None = None
    booking_affiliate_id: str | None = None
    booking_env: str = "sandbox"  # sandbox | production

    # LiteAPI (§9) — self-serve data feed for detection (read-only; no bookings).
    # Sandbox key is enough for dev/validation; see docs/price-source-migration.md.
    liteapi_key: str | None = None
    liteapi_country_code: str | None = None  # optional ISO-2 filter for hotel lookup
    liteapi_guest_nationality: str = "LV"  # required by the rates endpoint

    # Parse (§6a): booking parser + LLM model
    parser: str = "mock"  # mock | claude
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"

    # Inbound email (§5)
    inbound_webhook_secret: str | None = None  # optional shared secret on the webhook URL

    # Stripe (§11)
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id: str | None = None

    # Klaviyo (§10)
    klaviyo_api_key: str | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_url(self) -> str:
        """The URL to hand SQLAlchemy/Alembic.

        Managed hosts (Render, the Heroku lineage) still issue `postgres://`
        URLs, a scheme SQLAlchemy 2 refuses to load a dialect for. Everything
        that builds an engine must go through this, not `database_url`.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg2://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
