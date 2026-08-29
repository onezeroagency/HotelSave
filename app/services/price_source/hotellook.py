"""Travelpayouts / Hotellook price source (§9).

Implements the PriceSource interface against the Hotellook Data API:
  - resolve_hotel  → GET /api/v2/lookup.json           (token auth, no signature)
  - check          → POST-less async search:
                       GET /api/v2/search/start.json    (md5-signed, marker-tagged)
                       GET /api/v2/search/getResult.json (md5-signed, polled)

The search returns per-room offers carrying exactly what §7 needs — an OTA name,
a total, a free-cancellation (`refundable`) flag, and a marker-tagged rebook
deep-link (`fullBookingURL`). Occupancy (adults/children) is a *search* parameter,
so every returned room already matches the job's occupancy.

Signature (Travelpayouts): md5("<token>:<marker>:" + values of the request params
sorted alphabetically by key, joined by ":"). Pinned by a documented test vector
in the tests. Response field names follow the Hotellook v2 schema.

DEFUNCT — DO NOT USE IN PRODUCTION. The Hotellook Data API was permanently shut down
when Travelpayouts closed Hotellook (20 Oct 2025). As of the 2026-08-02 live run,
every endpoint under https://engine.hotellook.com/api/v2/ (lookup.json,
search/start.json, search/getResult.json — and the host root) returns an nginx 404,
so this source can never return a rate: `check()` catches the HTTP errors and yields
an empty list, meaning "no drop, ever". The request shapes below were written from
the API docs but were never validated against a live service (the build environment
blocked the host, and by the time the validation script was run locally the provider
was already gone). Kept as the worked reference implementation behind the PriceSource
interface. A replacement aggregator must be chosen — see docs/price-source-migration.md.
"""

import hashlib
import logging
import time
from datetime import date
from decimal import Decimal

import httpx

from ...config import settings
from .base import HotelMatch, PriceSource, RateCandidate

logger = logging.getLogger("hotelsave.hotellook")

BASE = "https://engine.hotellook.com/api/v2"
LOOKUP_URL = f"{BASE}/lookup.json"
SEARCH_START_URL = f"{BASE}/search/start.json"
SEARCH_RESULT_URL = f"{BASE}/search/getResult.json"

MAX_POLLS = 8
POLL_INTERVAL_SECONDS = 1.5
HTTP_TIMEOUT = 20.0


def _signature(token: str, marker: str, params: dict) -> str:
    """md5("token:marker:" + param values sorted alphabetically by key)."""
    tail = ":".join(str(params[k]) for k in sorted(params))
    return hashlib.md5(f"{token}:{marker}:{tail}".encode()).hexdigest()


class HotellookPriceSource(PriceSource):
    def __init__(self) -> None:
        if not settings.travelpayouts_token or not settings.travelpayouts_marker:
            raise RuntimeError(
                "Hotellook price source needs TRAVELPAYOUTS_TOKEN and TRAVELPAYOUTS_MARKER"
            )
        self._token = settings.travelpayouts_token
        self._marker = settings.travelpayouts_marker
        self._customer_ip = settings.travelpayouts_customer_ip
        self._client = httpx.Client(timeout=HTTP_TIMEOUT)
        # The upstream API is gone (Hotellook closed 20 Oct 2025) — flag it loudly
        # so a `PRICE_SOURCE=hotellook` deployment doesn't silently detect no drops.
        logger.warning(
            "HotellookPriceSource is DEFUNCT: the Hotellook API was shut down "
            "(20 Oct 2025) and now returns HTTP 404 for every request, so it will "
            "never surface a rate. Pick a replacement — see docs/price-source-migration.md."
        )

    # -- hotel identity (§6b) -------------------------------------------------

    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        country: str | None = None,
    ) -> list[HotelMatch]:
        query = f"{name} {city}".strip() if city else name
        resp = self._client.get(
            LOOKUP_URL,
            params={
                "query": query,
                "lang": "en",
                "lookFor": "hotel",
                "limit": 10,
                "token": self._token,
            },
        )
        resp.raise_for_status()
        hotels = resp.json().get("results", {}).get("hotels", [])

        # A single hit is high-confidence and auto-accepted; multiple hits are
        # ambiguous, so we hand them back as a pick-list (§6b).
        confidence = 0.95 if len(hotels) == 1 else 0.5
        return [
            HotelMatch(
                hotel_id=str(h.get("id")),
                name=h.get("label") or h.get("fullName") or name,
                city=h.get("locationName"),
                country=None,
                lat=lat,
                lng=lng,
                confidence=confidence,
            )
            for h in hotels
        ]

    # -- pricing (§7, §8) -----------------------------------------------------

    def check(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> list[RateCandidate]:
        try:
            search_id = self._start_search(hotel_id, check_in, check_out, adults, children)
            if search_id is None:
                return []
            hotels = self._poll_results(search_id)
        except httpx.HTTPError:
            logger.exception("Hotellook search failed for hotel_id=%s", hotel_id)
            return []

        return self._to_candidates(hotels, hotel_id, adults, children)

    def _start_search(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> int | None:
        children = children or 0
        # Signed params: exactly what we send, sorted alphabetically in the signature.
        params: dict = {
            "adultsCount": adults or 2,
            "checkIn": check_in.isoformat(),
            "checkOut": check_out.isoformat(),
            "childrenCount": children,
            "currency": "EUR",
            "hotelId": hotel_id,
            "lang": "en",
            "waitForResult": 0,
        }
        if self._customer_ip:
            params["customerIP"] = self._customer_ip

        signed = dict(params)
        signed["marker"] = self._marker
        signed["signature"] = _signature(self._token, self._marker, params)

        resp = self._client.get(SEARCH_START_URL, params=signed)
        resp.raise_for_status()
        return resp.json().get("searchId")

    def _poll_results(self, search_id: int) -> list[dict]:
        # getResult signature covers a fixed param set; all must be sent.
        result_params = {
            "limit": 1,
            "offset": 0,
            "roomsCount": 1,
            "searchId": search_id,
            "sortAsc": 1,
            "sortBy": "price",
        }
        query = dict(result_params)
        query["marker"] = self._marker
        query["signature"] = _signature(self._token, self._marker, result_params)

        # The search runs across OTAs asynchronously; poll until rooms appear.
        for attempt in range(MAX_POLLS):
            resp = self._client.get(SEARCH_RESULT_URL, params=query)
            resp.raise_for_status()
            hotels = resp.json().get("result", [])
            if any(h.get("rooms") for h in hotels):
                return hotels
            if attempt < MAX_POLLS - 1:
                time.sleep(POLL_INTERVAL_SECONDS)
        return hotels

    def _to_candidates(
        self, hotels: list[dict], hotel_id: str, adults: int | None, children: int | None
    ) -> list[RateCandidate]:
        candidates: list[RateCandidate] = []
        for hotel in hotels:
            if str(hotel.get("id")) != str(hotel_id):
                continue  # defensive: only our hotel
            for room in hotel.get("rooms", []):
                options = room.get("options") or {}
                total = room.get("total")
                if total is None:
                    continue
                candidates.append(
                    RateCandidate(
                        ota=room.get("agencyName") or "Unknown",
                        total_price=Decimal(str(total)),
                        currency="EUR",
                        # API exposes only a breakfast bool — see module note (§7 limitation).
                        board_type="BB" if options.get("breakfast") else "RO",
                        adults=adults,
                        children=children,
                        refundable=bool(options.get("refundable")),
                        deep_link=room.get("fullBookingURL") or room.get("bookingURL"),
                    )
                )
        return candidates
