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

LIVE-VALIDATED 2026-08-20 (sandbox key, scripts/validate_liteapi.py, "Amrita
Hotel" Liepaja LV → lp52d95): lookup params, rates body, and every mapped field
confirmed against the live service — retailRate.total[].amount, refundableTag
(both RFN and NRFN observed), boardType 'BI'→BB and 'RO'. 200 rates parsed clean.

TAX-INCLUSIVE TOTALS (§7): retailRate.taxesAndFees can carry entries with
included=false (VAT observed live on lp52d95), meaning `total` EXCLUDES a tax the
user's original OTA-style booking total includes. Comparing an ex-tax candidate
against a tax-inclusive original manufactures phantom "drops", so this adapter
adds every non-included, same-currency tax/fee to the candidate total before it
leaves here — RateCandidate.total_price is always the all-in comparable price.
A non-included fee in a *different* currency can't be added safely; that rate is
skipped (logged) rather than under-priced.
"""

import logging
from datetime import date, datetime
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


# The exact key/format inside cancelPolicyInfos is documented only as
# "cancellation time" (SDK README), with no sample payload to copy, and this
# adapter is not written from guesswork (see the Hotellook post-mortem). So try
# the plausible spellings, and log the keys actually present when none match —
# one production line then settles the shape for good.
_CANCEL_TIME_KEYS = ("cancelTime", "cancellationTime", "cancel_time", "deadline", "from")
_CANCEL_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _free_cancellation_until(rate: dict, hotel_id: str) -> datetime | None:
    """The moment this rate stops being free to cancel, or None if unverifiable."""
    policies = rate.get("cancellationPolicies") or {}
    infos = policies.get("cancelPolicyInfos") or []
    if isinstance(infos, dict):  # docs describe it as both Array and Object
        infos = [infos]

    stamps: list[datetime] = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        raw = next((info[k] for k in _CANCEL_TIME_KEYS if info.get(k)), None)
        if raw is None:
            logger.warning(
                "LiteAPI cancelPolicyInfos on %s has no recognised time key; keys=%s",
                hotel_id,
                sorted(info),
            )
            continue
        parsed = _parse_stamp(str(raw))
        if parsed is None:
            logger.warning("LiteAPI cancel time %r on %s not in a known format", raw, hotel_id)
            continue
        stamps.append(parsed)

    # Several tiers can apply (free until X, 50% until Y). The free window ends
    # at the EARLIEST stated boundary — taking the latest would tell the user
    # they have longer to act than they do.
    return min(stamps) if stamps else None


def _parse_stamp(raw: str) -> datetime | None:
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _CANCEL_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


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
        country: str | None = None,
    ) -> list[HotelMatch]:
        # LiteAPI rejects /data/hotels with 400 unless the search is scoped by
        # countryCode (confirmed live 2026-08-29). Prefer the booking's own
        # country; fall back to the deployment default for bookings that don't
        # state one. ISO-3166 alpha-2 only — anything else is not a country code.
        code = (country or "").strip().upper()
        if len(code) != 2:
            code = (settings.liteapi_country_code or "").strip().upper()

        params: dict = {"hotelName": name, "limit": 10}
        if city:
            params["cityName"] = city
        if len(code) == 2:
            params["countryCode"] = code
        else:
            # Without a country the request would 400; say so rather than
            # burning a call and failing opaquely (§6b then asks the user).
            logger.warning(
                "No country for %r (%s) — LiteAPI lookup needs a countryCode; "
                "set LITEAPI_COUNTRY_CODE as a default or capture it in the booking.",
                name,
                city,
            )
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

        return self._to_candidates(hotels, hotel_id)

    def _to_candidates(self, hotels: list[dict], hotel_id: str) -> list[RateCandidate]:
        candidates: list[RateCandidate] = []
        for hotel in hotels:
            if str(hotel.get("hotelId")) != str(hotel_id):
                continue  # defensive: only our hotel
            for room_type in hotel.get("roomTypes", []) or []:
                for rate in room_type.get("rates", []) or []:
                    retail = rate.get("retailRate") or {}
                    totals = retail.get("total") or []
                    if not totals:
                        continue
                    amount = totals[0].get("amount")
                    if amount is None:
                        continue
                    currency = totals[0].get("currency") or "EUR"
                    # §7: make the total all-in — LiteAPI can report taxes/fees
                    # (e.g. VAT) with included=false, excluded from `total`.
                    total = Decimal(str(amount))
                    skip = False
                    for fee in retail.get("taxesAndFees") or []:
                        if fee.get("included") or fee.get("amount") is None:
                            continue
                        if (fee.get("currency") or currency) != currency:
                            logger.warning(
                                "Skipping rate on %s: non-included fee in %s vs total in %s",
                                hotel_id,
                                fee.get("currency"),
                                currency,
                            )
                            skip = True
                            break
                        total += Decimal(str(fee["amount"]))
                    if skip:
                        continue
                    policies = rate.get("cancellationPolicies") or {}
                    # Occupancy as the *rate* reports it, not as we asked. Echoing
                    # the request made the §7 occupancy check a tautology that
                    # could never fail; None means "provider didn't say", which
                    # §7 treats as unknown rather than as a match.
                    occ = rate.get("occupancyNumber") or rate.get("maxOccupancy")
                    candidates.append(
                        RateCandidate(
                            # Wholesale feed: LiteAPI is the counterparty, not an OTA.
                            ota="LiteAPI",
                            total_price=total,
                            currency=currency,
                            board_type=_board_type(rate),
                            adults=int(occ) if isinstance(occ, (int, str)) and str(occ).isdigit() else None,
                            children=None,
                            refundable=policies.get("refundableTag") == "RFN",
                            # Detection-only feed — the rebook link is the affiliate
                            # layer's job (docs/price-source-migration.md §8).
                            deep_link=None,
                            room_name=(rate.get("name") or room_type.get("name")),
                            free_cancellation_until=_free_cancellation_until(rate, hotel_id),
                        )
                    )
        return candidates
