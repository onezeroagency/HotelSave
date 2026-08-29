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

    # Browser origins allowed to call the API (the marketing site + local dev).
    # Comma-separated; never "*" — these requests carry an auth token.
    cors_origins: str = (
        "https://myroomwatch.com,https://www.myroomwatch.com,"
        "http://localhost:8000,http://127.0.0.1:5500"
    )

    # Auth
    secret_key: str = "dev-insecure-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7
    algorithm: str = "HS256"

    # Price source (§9)
    price_source: str = "mock"  # mock | hotellook
    drop_floor_abs: float = 10.0
    drop_floor_pct: float = 0.03
    deadline_alert_hours: int = 48

    # Only alert on a drop the user can actually act on — i.e. one carrying a
    # rebook link. Live 2026-08-29: a Riga booking made 20 minutes earlier at the
    # cheapest refundable rate Booking.com sells (EUR 436) was reported as a
    # EUR 94.91 drop, because the detection feed prices a different supplier's
    # inventory. Booking's own floor that day was EUR 436 refundable / EUR 394
    # non-refundable, so the rate was real but unreachable — no link, not on the
    # channel the user booked. Promising a saving nobody can collect is worse
    # than staying quiet, so those alerts are held. Flip to False only when the
    # price source returns working deep-links (affiliate program live).
    require_rebook_url_for_alerts: bool = True

    # Where "check today's price" sends the user (§9 mode A). Job-level search,
    # not a rate-level deep link — see app/services/rebook.py for why.
    # VERIFY THE PARAM NAMES IN A BROWSER before trusting a new base URL; they
    # are an OTA's public search interface, not a documented API.
    rebook_search_base_url: str = "https://www.booking.com/searchresults.html"
    # Raw query string appended to every check link (e.g. "aid=304142&label=mrw").
    # Set this the day an affiliate program is approved — links start earning
    # with no code change. Unset, links still work and simply earn nothing.
    rebook_affiliate_params: str | None = None

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
    # Required only for identity-linked API keys, which 400 without it; leave
    # blank for workspace-scoped keys (see app/services/parser/claude.py).
    anthropic_workspace_id: str | None = None
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
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
