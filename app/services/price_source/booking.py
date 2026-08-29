"""Booking.com Demand API price source (§9) — affiliate replacement for Hotellook.

Chosen 2026-08-02 after Hotellook was shut down (see docs/price-source-migration.md).
The Demand API is the affiliate route that *also* exposes an availability endpoint,
so one integration powers both detection (§7/§8) and the attributed rebook deep-link.

SCAFFOLD — NOT YET IMPLEMENTED. Only the stable, verifiable plumbing is encoded here:
the base URLs and the header auth scheme (from Booking's public docs). The request
bodies and response-field mapping are deliberately left as TODOs, to be written and
validated against the Booking **sandbox** once a Managed Affiliate Partner API key +
X-Affiliate-Id exist. Writing that mapping from docs alone — unvalidated — is exactly
how the previous (Hotellook) integration ended up dead on arrival, so we don't repeat
it. Onboarding + the field-by-field mapping plan live in docs/price-source-migration.md §8.
"""

import logging
from datetime import date

import httpx

from ...config import settings
from .base import HotelMatch, PriceSource, RateCandidate

logger = logging.getLogger("hotelsave.booking")

# Verified facts (Booking.com Demand API docs, 2026-08):
BASE_URLS = {
    "sandbox": "https://demandapi-sandbox.booking.com/3.1",
    "production": "https://demandapi.booking.com/3.1",
}
HTTP_TIMEOUT = 20.0


class BookingPriceSource(PriceSource):
    def __init__(self) -> None:
        if not settings.booking_api_key or not settings.booking_affiliate_id:
            raise RuntimeError(
                "Booking.com price source needs BOOKING_API_KEY and BOOKING_AFFILIATE_ID "
                "from a DIRECT Managed Affiliate Partner account — see "
                "docs/price-source-migration.md."
            )
        env = (settings.booking_env or "sandbox").lower()
        if env not in BASE_URLS:
            raise ValueError(f"BOOKING_ENV must be one of {sorted(BASE_URLS)}, got {env!r}")
        self._base_url = BASE_URLS[env]
        self._client = httpx.Client(timeout=HTTP_TIMEOUT, headers=self._auth_headers())
        logger.info("BookingPriceSource initialised (env=%s, base=%s)", env, self._base_url)

    def _auth_headers(self) -> dict[str, str]:
        # Demand API auth (docs-verified): affiliate id + bearer token, JSON body.
        return {
            "X-Affiliate-Id": str(settings.booking_affiliate_id),
            "Authorization": f"Bearer {settings.booking_api_key}",
            "Content-Type": "application/json",
        }

    # -- hotel identity (§6b) -------------------------------------------------

    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        country: str | None = None,
    ) -> list[HotelMatch]:
        # TODO(booking): POST the accommodations search (name + city) to
        # f"{self._base_url}/accommodations/..." and map results → HotelMatch,
        # keeping the §6b confidence rule (single strong match → 0.95, multiple → 0.5).
        # Confirm the exact path + response field names against the sandbox first.
        raise NotImplementedError(
            "BookingPriceSource.resolve_hotel is not implemented yet — pending sandbox "
            "validation with a real API key (docs/price-source-migration.md §8)."
        )

    # -- pricing (§7, §8) -----------------------------------------------------

    def check(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> list[RateCandidate]:
        # TODO(booking): POST the accommodations availability request for the property
        # + dates + occupancy to f"{self._base_url}/accommodations/..." and map each
        # room rate → RateCandidate: refundable ← cancellation policy, board_type ←
        # meal plan (RO/BB/HB/FB), total_price/currency ← rate, deep_link ← the
        # affiliate-attributed booking URL. Validate every field against the sandbox
        # before trusting it (the Hotellook lesson).
        raise NotImplementedError(
            "BookingPriceSource.check is not implemented yet — pending sandbox "
            "validation with a real API key (docs/price-source-migration.md §8)."
        )
