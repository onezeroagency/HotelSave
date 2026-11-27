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
    reset_token_expire_minutes: int = 60
    # Where password-reset links point (the marketing site, which serves
    # reset.html). No trailing slash.
    site_base_url: str = "https://www.myroomwatch.com"
    # Where the API itself is reachable (magic links in emails point here).
    api_base_url: str = "https://api.myroomwatch.com"

    # Passwordless sign-in (magic links)
    magic_link_expire_minutes: int = 15
    # Rate limits for POST /auth/magic-link: N requests per window.
    magic_link_email_limit: int = 3
    magic_link_ip_limit: int = 10
    magic_link_window_seconds: int = 900

    # Postmark outbound (magic-link emails). Without a token the mailer
    # dry-runs to the log, same contract as the Klaviyo client.
    postmark_server_token: str | None = None
    postmark_from_email: str = "hello@myroomwatch.com"
    postmark_message_stream: str = "outbound"

    # Price source (§9)
    price_source: str = "mock"  # mock | google_hotels | liteapi | booking | hotellook(defunct)

    # Google Hotels via SerpApi (§9) — the watch engine from 2026-11. Public-
    # market prices (the OTAs users actually book on), so movement seen here is
    # movement in the user's own market; also keeps the daily watch off
    # LiteAPI, whose ToS prohibit benchmarking and expect a 5000:1
    # search-to-book ratio no watch-only product can meet. NOT yet
    # live-validated: run scripts/validate_google_hotels.py with a real key
    # before setting PRICE_SOURCE=google_hotels in production.
    serpapi_key: str | None = None
    serpapi_base_url: str = "https://serpapi.com/search"
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

    # Meta Pixel / Conversions API (ad measurement). Without a token the CAPI
    # client dry-runs to the log, same contract as Klaviyo/Stripe.
    meta_pixel_id: str = "955713480660923"
    meta_capi_token: str | None = None
    meta_capi_base_url: str = "https://graph.facebook.com/v21.0"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_url(self) -> str:
        """The URL to hand SQLAlchemy/Alembic.

        Everything that builds an engine must go through this, not
        `database_url`, and the Postgres driver is always named EXPLICITLY.
        Two production incidents say why:

        - Managed hosts (Render, the Heroku lineage) issue `postgres://`,
          a scheme SQLAlchemy 2 refuses to load a dialect for (deploy
          failure, Aug 2026).
        - A bare `postgresql://` leaves the driver choice to SQLAlchemy's
          default, which CHANGED under us: 2.0 picked psycopg2, 2.1 picks
          psycopg v3 — not in our dependencies, so the first deploy after
          2.1.9 landed died in pre-deploy with ModuleNotFoundError:
          psycopg (Nov 2026). An explicit `+psycopg2` is immune to
          whatever a future default prefers.
        """
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return url.replace(prefix, "postgresql+psycopg2://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
