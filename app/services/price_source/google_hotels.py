"""Google Hotels (via SerpApi) price source — the daily watch engine (§9).

WHY THIS SOURCE EXISTS (24 Nov 2026): the watch moved off LiteAPI for two
reasons, both load-bearing.

 1. Market alignment. LiteAPI prices its own supplier inventory; users book on
    public OTAs. Comparing the two manufactured phantom drops (the €94.91
    post-mortem in docs/price-source-migration.md). Google Hotels aggregates
    the public OTAs — including the one the user actually booked on — so
    movement seen here is movement in the user's own market.
 2. Terms. LiteAPI's ToS (read 2026-11-24) prohibit "using LiteAPI data for
    competitive analysis, benchmarking" and "mapping LiteAPI data to
    third-party sources". A watch that compares LiteAPI rates against a
    Booking.com booking, with a 5000:1 search-to-book expectation we never
    book against, sits badly under both. LiteAPI's role moves to the (future)
    verify-and-book step, where usage is exactly what its licence permits.

TRIGGER, NOT PROMISE: candidates from this source are deliberately
trigger-grade. Google's per-OTA price entries usually carry no room name and
no board, so like-for-like can't be fully established from here. They power
`lowest_seen_price`, the dashboard's "rates moved", and the deadline email's
mode-A nudge — all of which claim only "worth a look". They never carry a
`deep_link`, so the price-drop email (which quotes a figure) stays gated.
Quotable offers must come from a source that knows the room and the board.

NOT YET LIVE-VALIDATED. Shapes are from SerpApi's published docs
(serpapi.com/google-hotels-api and /google-hotels-property-details, read
2026-11-24 — the Hotellook rule: field names below are quoted, not guessed).
Run scripts/validate_google_hotels.py with a real key before pointing
production at this source; the free tier covers it.

Request shapes (both are GET {serpapi_base_url}, engine=google_hotels):
  search  — q, check_in_date, check_out_date, adults, children, currency,
            gl, hl → properties[].{name, property_token, gps_coordinates}
  details — property_token + the same date/occupancy params →
            prices[]          (per OTA: source, num_guests, rate_per_night,
                               total_rate, free_cancellation,
                               free_cancellation_until_date/_time)
            featured_prices[] (per OTA: source, rooms[].{name, num_guests,
                               rate_per_night, total_rate,
                               rates[].{breakfast_included, inclusions}})

MONEY FIELDS (§7, the tax trap): each rate object can carry
`extracted_display_price` / `extracted_before_taxes_fees` (PRE-tax) and
`extracted_after_taxes_fees` (all-in); `extracted_lowest` tracks the all-in
figure in the documented examples. The user's original total is tax-inclusive,
so comparing a pre-tax number against it manufactures drops. This adapter uses
after_taxes_fees, then lowest — and SKIPS an entry (logged) rather than fall
back to a pre-tax field.

free_cancellation_until_date arrives year-less ("Mar 17"), so it is NOT parsed
into free_cancellation_until — a wrong year inference could mis-rank a policy
against the user's deadline. Unknown never blocks a match on its own (§7 3b);
`refundable` is True only when the entry says free_cancellation explicitly.
"""

import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

import httpx

from ...config import settings
from .base import HotelMatch, PriceSource, RateCandidate

logger = logging.getLogger("hotelsave.google_hotels")

HTTP_TIMEOUT = 25.0

# Auto-resolve needs exactly one ≥0.9-confidence match (§6b contract shared by
# ingestion, the jobs router and the scheduler retry). Google's search is
# ranked full-text, so "one result" never happens naturally — instead a hit
# auto-resolves only when its name agrees with the booking's.
#
# LIVE LESSON (first real run, 2026-11-24): character similarity is the wrong
# tool. Google canonicalizes names — the booking said "Grand Poet Hotel and
# SPA by Semarah", Google said "GRAND POET by Semarah hotels", ratio 0.67 —
# and the RIGHT hotel, top-ranked, would have gone to ask-the-user. Hotels
# differ in their distinctive words, not their generic ones, so agreement is
# measured on tokens with the generics stripped; raw character similarity
# stays only as the fallback for names that are all generics.
_GENERIC_NAME_TOKENS = {
    "hotel", "hotels", "the", "and", "by", "spa", "resort", "resorts", "inn",
    "suites", "apartments", "aparthotel", "boutique", "guesthouse", "hostel",
}


def _name_tokens(name: str) -> set[str]:
    return {
        t
        for t in re.split(r"[^a-z0-9]+", name.strip().lower())
        if t and t not in _GENERIC_NAME_TOKENS
    }


def _names_agree(booked: str, candidate: str) -> bool:
    booked_t, candidate_t = _name_tokens(booked), _name_tokens(candidate)
    if booked_t and candidate_t:
        shared = booked_t & candidate_t
        if len(shared) >= 2 and len(shared) / len(booked_t) >= 0.6:
            return True
        if len(booked_t) == 1 and booked_t <= candidate_t:
            return True
    return SequenceMatcher(None, booked.strip().lower(), candidate.strip().lower()).ratio() >= 0.75


def _total_price(rate_block: dict | None) -> Decimal | None:
    """All-in total from a SerpApi money object, or None if only pre-tax
    figures exist — never silently compare a pre-tax number (§7)."""
    if not rate_block:
        return None
    for key in ("extracted_after_taxes_fees", "extracted_lowest"):
        value = rate_block.get(key)
        if value is not None:
            return Decimal(str(value))
    return None


def _board_from_room(room: dict, rates: list[dict]) -> str | None:
    """BB when breakfast is explicitly included; otherwise unknown (None).

    Absence of breakfast information is NOT room-only — Google omits board on
    most entries, and mapping silence to RO would wrongly reject BB originals
    under §7 rule 2."""
    name = (room.get("name") or "").lower()
    if "breakfast" in name:
        return "BB"
    for rate in rates:
        if rate.get("breakfast_included"):
            return "BB"
        inclusions = [str(i).lower() for i in (rate.get("inclusions") or [])]
        if any("breakfast" in inc for inc in inclusions):
            return "BB"
    return None


class GoogleHotelsPriceSource(PriceSource):
    def __init__(self) -> None:
        if not settings.serpapi_key:
            raise RuntimeError("Google Hotels price source needs SERPAPI_KEY")
        self._client = httpx.Client(timeout=HTTP_TIMEOUT)

    def _get(self, params: dict) -> dict | None:
        params = {
            "engine": "google_hotels",
            "currency": "EUR",
            "hl": "en",
            "api_key": settings.serpapi_key,
            **params,
        }
        resp = self._client.get(settings.serpapi_base_url, params=params)
        resp.raise_for_status()
        payload = resp.json()
        # SerpApi reports failures as {"error": "..."} in an HTTP-200 body.
        if isinstance(payload, dict) and payload.get("error"):
            logger.error("SerpApi error: %s", payload["error"])
            return None
        return payload

    # -- hotel identity (§6b) -------------------------------------------------

    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        country: str | None = None,
        check_in: date | None = None,
        check_out: date | None = None,
    ) -> list[HotelMatch]:
        # Google Hotels refuses a search without dates. Use the booking's when
        # the caller has them (availability-accurate); otherwise any valid
        # near-future window — the goal here is the property_token, which is
        # not date-scoped.
        if check_in is None or check_out is None:
            check_in = date.today() + timedelta(days=30)
            check_out = check_in + timedelta(days=2)

        params: dict = {
            "q": f"{name} {city}".strip() if city else name,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
        }
        code = (country or "").strip().lower()
        if len(code) == 2:
            params["gl"] = code

        try:
            payload = self._get(params)
        except httpx.HTTPError:
            logger.exception("Google Hotels search failed for %r (%s)", name, city)
            return []
        if payload is None:
            return []

        properties = payload.get("properties") or []
        matches: list[HotelMatch] = []
        for prop in properties[:10]:
            token = prop.get("property_token")
            if not token:
                continue
            gps = prop.get("gps_coordinates") or {}
            matches.append(
                HotelMatch(
                    hotel_id=str(token),
                    name=prop.get("name") or name,
                    city=city,  # search results don't echo a city field
                    country=country,
                    lat=gps.get("latitude", prop.get("latitude", lat)),
                    lng=gps.get("longitude", prop.get("longitude", lng)),
                    confidence=0.5,
                )
            )

        # Ranked search ≠ filtered lookup: return ONLY the first hit whose
        # name agrees with the booking's, at auto-resolve confidence. A list
        # of nearby alternatives would otherwise push every job into the
        # ask-the-user flow (§6b treats >1 match as ambiguous).
        for hit in matches:
            if _names_agree(name, hit.name):
                return [
                    HotelMatch(
                        hotel_id=hit.hotel_id,
                        name=hit.name,
                        city=hit.city,
                        country=hit.country,
                        lat=hit.lat,
                        lng=hit.lng,
                        confidence=0.95,
                    )
                ]
        return matches

    # -- pricing (§7, §8) -----------------------------------------------------

    def check(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> list[RateCandidate]:
        params = {
            "property_token": hotel_id,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "adults": adults or 2,
            "children": children or 0,
            # §7 rule 3 discards non-refundable candidates anyway, so ask
            # Google for free-cancellation rates only. LIVE LESSON
            # (2026-11-24): unfiltered, the per-entry free_cancellation flag
            # was absent on every price — the refundable rule matched nothing.
            "free_cancellation": "true",
        }
        try:
            payload = self._get(params)
        except httpx.HTTPError:
            logger.exception("Google Hotels details failed for token=%s", hotel_id)
            return []
        if payload is None:
            return []

        # Trust the filter ONLY when the response echoes it back: entries in a
        # filter-applied response are refundable by construction, but if
        # SerpApi ignored the unknown param (no echo), falling back to the
        # per-entry flag is what keeps us from marking prepaid rates
        # refundable. Verified live by the standalone validator's A/B run.
        echoed = (payload.get("search_parameters") or {}).get("free_cancellation")
        filter_applied = str(echoed).lower() == "true"

        candidates: list[RateCandidate] = []

        # OTA-level entries: no room identity, but they carry the explicit
        # free_cancellation flag — these are the movement signal.
        for entry in payload.get("prices") or []:
            total = _total_price(entry.get("total_rate"))
            if total is None:
                logger.info(
                    "Skipping %s on %s: no tax-inclusive total (pre-tax only)",
                    entry.get("source"),
                    hotel_id,
                )
                continue
            candidates.append(
                RateCandidate(
                    ota=entry.get("source") or "Google Hotels",
                    total_price=total,
                    currency="EUR",
                    board_type=None,  # unknown, never asserted
                    adults=entry.get("num_guests"),
                    children=None,
                    refundable=filter_applied or entry.get("free_cancellation") is True,
                    # Trigger-grade only — a link here would open the alert
                    # gate and quote a figure no room-level match backs up.
                    deep_link=None,
                    room_name=None,
                )
            )

        # Room-level entries (featured_prices): named rooms, sometimes board.
        for block in payload.get("featured_prices") or []:
            ota = block.get("source") or "Google Hotels"
            for room in block.get("rooms") or []:
                total = _total_price(room.get("total_rate"))
                if total is None:
                    continue
                rates = [r for r in (room.get("rates") or []) if isinstance(r, dict)]
                refundable = (
                    filter_applied
                    or room.get("free_cancellation") is True
                    or any(r.get("free_cancellation") is True for r in rates)
                )
                candidates.append(
                    RateCandidate(
                        ota=ota,
                        total_price=total,
                        currency="EUR",
                        board_type=_board_from_room(room, rates),
                        adults=room.get("num_guests"),
                        children=None,
                        refundable=refundable,
                        deep_link=None,
                        room_name=room.get("name"),
                    )
                )

        return candidates
