"""Live validation for the LiteAPI price source (§9).

The build environment blocks outbound access to api.liteapi.travel, so the
integration was written from the official SDK source but never run live. Run this
from any machine with normal internet (your laptop) — a SANDBOX key is enough:

    LITEAPI_KEY=sand_xxx PRICE_SOURCE=liteapi \
        python scripts/validate_liteapi.py "Amrita Hotel" "Liepaja" LV

It confirms the assumptions the offline build couldn't, printing the raw API
envelopes alongside the parsed output so any mismatch is obvious:

  1. lookup params        (GET /data/hotels: hotelName + cityName + countryCode)
  2. rates request body   (hotelIds/checkin/checkout/currency/guestNationality/occupancies)
  3. response field names (data[].roomTypes[].rates[].{retailRate.total[].amount,
                           cancellationPolicies.refundableTag, boardType, boardName})

Paste the output back and any needed fix is a one-line change.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

import httpx

from app.config import settings
from app.services.price_source.liteapi import (
    LOOKUP_URL,
    RATES_URL,
    LiteAPIPriceSource,
)


def _dump(label: str, obj) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    print(f"\n--- {label} ---")
    print(text[:2000] + (" …(truncated)" if len(text) > 2000 else ""))


def main() -> None:
    if not settings.liteapi_key:
        sys.exit("Set LITEAPI_KEY first (sandbox key from dashboard.liteapi.travel).")
    name = sys.argv[1] if len(sys.argv) > 1 else "Amrita Hotel"
    city = sys.argv[2] if len(sys.argv) > 2 else "Liepaja"
    country = sys.argv[3] if len(sys.argv) > 3 else "LV"

    headers = {"X-API-Key": settings.liteapi_key, "accept": "application/json"}
    client = httpx.Client(timeout=25.0, headers=headers)
    check_in = datetime.now(tz=UTC).date() + timedelta(days=40)
    check_out = check_in + timedelta(days=2)

    # 1) raw lookup (assumption 1: param names)
    print(f"# Lookup: hotelName={name!r} cityName={city!r} countryCode={country}")
    lu = client.get(
        LOOKUP_URL,
        params={"hotelName": name, "cityName": city, "countryCode": country, "limit": 10},
    )
    print(f"# GET /data/hotels  HTTP {lu.status_code}")
    body = lu.json() if lu.headers.get("content-type", "").startswith("application/json") else lu.text
    _dump("lookup body", body)
    hotels = (body.get("data") or []) if isinstance(body, dict) else []
    if not hotels:
        sys.exit("No hotels — check assumption 1 (lookup param names / country filter).")
    hotel_id = str(hotels[0].get("id") or hotels[0].get("hotelId"))
    _dump("first hotel keys", sorted(hotels[0].keys()))
    print(f"\nUsing hotel_id={hotel_id}, {check_in} → {check_out}")

    # 2) parsed matches via the real code path
    settings.liteapi_country_code = country  # so resolve_hotel filters the same way
    source = LiteAPIPriceSource()
    matches = source.resolve_hotel(name, city)
    _dump("resolve_hotel() matches", [m.__dict__ for m in matches])

    # 3) raw rates (assumptions 2 + 3)
    payload = {
        "hotelIds": [hotel_id],
        "checkin": check_in.isoformat(),
        "checkout": check_out.isoformat(),
        "currency": "EUR",
        "guestNationality": settings.liteapi_guest_nationality,
        "occupancies": [{"adults": 2, "children": []}],
    }
    rr = client.post(RATES_URL, json=payload)
    print(f"\n# POST /hotels/rates  HTTP {rr.status_code}")
    rbody = rr.json() if rr.headers.get("content-type", "").startswith("application/json") else rr.text
    data = (rbody.get("data") or []) if isinstance(rbody, dict) else []
    first = data[0] if data else {}
    _dump("rates: first hotel envelope", first if first else rbody)
    room_types = first.get("roomTypes") or []
    rate = (room_types[0].get("rates") or [{}])[0] if room_types else {}
    if rate:
        _dump("first rate keys", sorted(rate.keys()))
        _dump("first rate.retailRate", rate.get("retailRate"))
        _dump("first rate.cancellationPolicies", rate.get("cancellationPolicies"))

    # 4) parsed candidates via the real code path
    candidates = source.check(hotel_id, check_in, check_out, 2, 0)
    _dump("check() candidates", [c.__dict__ for c in candidates])

    # 5) assumption checklist
    totals = (rate.get("retailRate") or {}).get("total") or []
    policies = rate.get("cancellationPolicies") or {}
    print("\n=== ASSUMPTION CHECK ===")
    print(f"1. lookup returned hotels        : {'OK' if hotels else 'FAIL'} ({len(hotels)} hotels)")
    print(f"2. rates returned roomTypes      : {'OK' if room_types else 'FAIL'} ({len(room_types)} roomTypes)")
    print(f"3. field: retailRate.total.amount: {'OK' if totals and 'amount' in totals[0] else 'MISSING'}")
    print(f"   field: refundableTag          : {'OK' if 'refundableTag' in policies else 'MISSING'} (value={policies.get('refundableTag')!r})")
    print(f"   field: boardType/boardName    : boardType={rate.get('boardType')!r} boardName={rate.get('boardName')!r}")
    print(f"\nParsed {len(candidates)} candidate(s); "
          f"{sum(1 for c in candidates if c.refundable)} refundable.")


if __name__ == "__main__":
    main()
