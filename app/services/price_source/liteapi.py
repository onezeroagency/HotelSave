"""LiteAPI price source (§9) — the self-serve data feed for drop detection.

Chosen as the working data source after Booking.com's affiliate/Demand API route
stalled (NA program rejection; Demand API gated — see docs/price-source-migration.md).
LiteAPI is a wholesale booking API, but HotelSave uses it READ-ONLY: rates power
detection, while the rebook path stays an affiliate deep-link (added separately
once an OTA affiliate account exists), so `deep_link` is None here.

Endpoints (from the official liteapi-travel/nodejs-sdk source, v3.0):
  - resolve_hotel → GET  https://api.liteapi.travel/v3.0/data/hotels
                        ?hotelName=&cityName=&countryCode=   (X-API-Key header)
  - check         → POST https://api.liteapi.travel/v3.0/hotels/rates
                        {hotelIds, checkin, checkout, currency,
                         guestNationality, occupancies}

Response shape (SDK tests + docs): {"data": [{"hotelId", "roomTypes": [
  {"offerId", "rates": [{"name", "boardType", "boardName",
    "retailRate": {"total": [{"amount", "currency"}]},
    "cancellationPolicies": {"refundableTag": "RFN"|"NRFN"}}]}]}]}

PENDING LIVE VALIDATION: the build environment blocks api.liteapi.travel, so this
was written from the SDK source but not yet run live. Run
scripts/validate_liteapi.py from a machine with normal internet (sandbox key is
enough); it prints raw envelopes next to parsed output so any field mismatch is a
one-line fix. Points to confirm: lookup param names, rate-field nesting,
boardType vocabulary, and whether children ages (we only know the count) matter.
"""

import logging
from datetime import date
from decimal import Decimal

import httpx

from ...config import settings
from .base import HotelMatch, PriceSource, RateCandidate

logger = logging.getLogger("hotelsave.liteapi")

DATA_BASE = "https://api.liteapi.travel/v3.0"
LOOKUP_URL = f"{DATA_BASE}/data/hotels"
RATES_URL = f"{DATA_BASE}/hotels/rates"

HTTP_TIMEOUT = 25.0

# LiteAPI board codes → our §7 vocabulary (RO/BB/HB/FB/AI). Anything unknown falls
# back to a boardName keyword check; validation prints the raw values so this map
# can be corrected from real data.
_BOARD_MAP = {
    "RO": "RO",
    "BI": "BB",  # "breakfast included"
    "BB": "BB",
    "HB": "HB",
    "FB": "FB",
    "AI": "AI",
}


def _board_type(rate: dict) -> str | None:
    code = (rate.get("boardType") or "").upper()
    if code in _BOARD_MAP:
        return _BOARD_MAP[code]
    name = (rate.get("boardName") or "").lower()
    if "breakfast" in name:
        return "BB"
    if "half board" in name:
        return "HB"
    if "full board" in name:
        return "FB"
    if "all inclusive" in name or "all-inclusive" in name:
        return "AI"
    if "room only" in name or not name:
        return "RO" if code or name else None
    return None


class LiteAPIPriceSource(PriceSource):
    def __init__(self) -> None:
        if not settings.liteapi_key:
            raise RuntimeError("LiteAPI price source needs LITEAPI_KEY (sandbox or prod)")
        self._client = httpx.Client(
            timeout=HTTP_TIMEOUT,
            headers={"X-API-Key": settings.liteapi_key, "accept": "application/json"},
        )

    # -- hotel identity (§6b) -------------------------------------------------

    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[HotelMatch]:
        params: dict = {"hotelName": name, "limit": 10}
        if city:
            params["cityName"] = city
        if settings.liteapi_country_code:
            params["countryCode"] = settings.liteapi_country_code
        resp = self._client.get(LOOKUP_URL, params=params)
        resp.raise_for_status()
        hotels = resp.json().get("data", []) or []

        confidence = 0.95 if len(hotels) == 1 else 0.5
        return [
            HotelMatch(
                hotel_id=str(h.get("id") or h.get("hotelId")),
                name=h.get("name") or name,
                city=h.get("city"),
                country=h.get("country"),
                lat=h.get("latitude", lat),
                lng=h.get("longitude", lng),
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
        # LiteAPI wants children as an ages array; jobs only store a count, so a
        # mid-range placeholder age stands in (validation confirms it's accepted).
        occupancy: dict = {"adults": adults or 2, "children": [8] * (children or 0)}
        payload = {
            "hotelIds": [hotel_id],
            "checkin": check_in.isoformat(),
            "checkout": check_out.isoformat(),
            "currency": "EUR",
            "guestNationality": settings.liteapi_guest_nationality,
            "occupancies": [occupancy],
        }
        try:
            resp = self._client.post(RATES_URL, json=payload)
            resp.raise_for_status()
            hotels = resp.json().get("data", []) or []
        except httpx.HTTPError:
            logger.exception("LiteAPI rates search failed for hotel_id=%s", hotel_id)
            return []

        return self._to_candidates(hotels, hotel_id, adults, children)

    def _to_candidates(
        self, hotels: list[dict], hotel_id: str, adults: int | None, children: int | None
    ) -> list[RateCandidate]:
        candidates: list[RateCandidate] = []
        for hotel in hotels:
            if str(hotel.get("hotelId")) != str(hotel_id):
                continue  # defensive: only our hotel
            for room_type in hotel.get("roomTypes", []) or []:
                for rate in room_type.get("rates", []) or []:
                    totals = (rate.get("retailRate") or {}).get("total") or []
                    if not totals:
                        continue
                    amount = totals[0].get("amount")
                    if amount is None:
                        continue
                    policies = rate.get("cancellationPolicies") or {}
                    candidates.append(
                        RateCandidate(
                            # Wholesale feed: LiteAPI is the counterparty, not an OTA.
                            ota="LiteAPI",
                            total_price=Decimal(str(amount)),
                            currency=totals[0].get("currency") or "EUR",
                            board_type=_board_type(rate),
                            adults=adults,
                            children=children,
                            refundable=policies.get("refundableTag") == "RFN",
                            # Detection-only feed — the rebook link is the affiliate
                            # layer's job (docs/price-source-migration.md §8).
                            deep_link=None,
                        )
                    )
        return candidates
