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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
