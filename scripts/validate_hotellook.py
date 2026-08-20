"""Live validation for the Hotellook price source (§9).

OUTCOME (2026-08-02 live run against "Amrita Hotel Liepaja"): the validation
CANNOT pass — not because of a bug in our request shapes, but because the
Hotellook API no longer exists. Travelpayouts permanently closed Hotellook on
20 Oct 2025; every endpoint under https://engine.hotellook.com/api/v2/ (lookup,
search/start, search/getResult — and the host root) now returns an nginx 404.
This script is retained only as the historical harness that surfaced that fact;
it will fail at the resolve_hotel() step with a 404. The path forward is a
replacement aggregator — see docs/price-source-migration.md.

The build environment blocks outbound access to engine.hotellook.com, so the
integration was written from the API docs but never run live. Run this from any
machine that CAN reach the API (your laptop, or a cloud environment whose network
policy allows engine.hotellook.com):

    TRAVELPAYOUTS_TOKEN=xxx TRAVELPAYOUTS_MARKER=yyy \
        python scripts/validate_hotellook.py "Amrita Hotel Liepaja"

It confirms the three things the offline build couldn't, printing the raw API
envelopes alongside the parsed output so any mismatch is obvious:

  1. the hotel search param  (we send `hotelId`)
  2. poll-until-complete     (does getResult return rooms within the poll budget?)
  3. response field names    (`result[].rooms[].{total,agencyName,options.refundable,fullBookingURL}`)

Paste the output back and any needed fix is a one-line change.
"""

import json
import sys
import time
from datetime import date, timedelta

import httpx

from app.config import settings
from app.services.price_source.hotellook import (
    SEARCH_RESULT_URL,
    SEARCH_START_URL,
    HotellookPriceSource,
    _signature,
)


def _dump(label: str, obj) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    print(f"\n--- {label} ---")
    print(text[:1500] + (" …(truncated)" if len(text) > 1500 else ""))


def main() -> None:
    if not settings.travelpayouts_token or not settings.travelpayouts_marker:
        sys.exit("Set TRAVELPAYOUTS_TOKEN and TRAVELPAYOUTS_MARKER first.")
    query = sys.argv[1] if len(sys.argv) > 1 else "Amrita Hotel Liepaja"

    source = HotellookPriceSource()
    token, marker = settings.travelpayouts_token, settings.travelpayouts_marker
    client = httpx.Client(timeout=25.0)
    check_in = date.today() + timedelta(days=40)
    check_out = check_in + timedelta(days=2)

    # 1) resolve_hotel
    print(f"# Resolving: {query!r}")
    matches = source.resolve_hotel(query)
    _dump("resolve_hotel() matches", [m.__dict__ for m in matches])
    if not matches:
        sys.exit("No hotel matches — try a different query (check assumption: lookup fields).")
    hotel_id = matches[0].hotel_id
    print(f"\nUsing hotel_id={hotel_id}, {check_in} → {check_out}")

    # 2) raw start.json (assumption 1: hotelId param)
    params = {
        "adultsCount": 2,
        "checkIn": check_in.isoformat(),
        "checkOut": check_out.isoformat(),
        "childrenCount": 0,
        "currency": "EUR",
        "hotelId": hotel_id,
        "lang": "en",
        "waitForResult": 0,
    }
    signed = {**params, "marker": marker, "signature": _signature(token, marker, params)}
    start = client.get(SEARCH_START_URL, params=signed)
    print(f"\n# start.json  HTTP {start.status_code}")
    _dump("start.json body", start.json() if start.is_success else start.text)
    search_id = start.json().get("searchId") if start.is_success else None
    if not search_id:
        sys.exit("No searchId — assumption 1 (hotelId param) or signature needs adjustment.")

    # 3) raw getResult.json polling (assumptions 2 + 3)
    rp = {"limit": 1, "offset": 0, "roomsCount": 1, "searchId": search_id, "sortAsc": 1, "sortBy": "price"}
    q = {**rp, "marker": marker, "signature": _signature(token, marker, rp)}
    hotels, polls = [], 0
    for polls in range(1, 9):
        data = client.get(SEARCH_RESULT_URL, params=q).json()
        hotels = data.get("result", [])
        if any(h.get("rooms") for h in hotels):
            break
        time.sleep(1.5)
    print(f"\n# getResult.json returned rooms after {polls} poll(s)")
    first = next((h for h in hotels if h.get("rooms")), hotels[0] if hotels else {})
    _dump("getResult.json first hotel", first)
    if first.get("rooms"):
        _dump("first room keys", sorted(first["rooms"][0].keys()))
        _dump("first room.options keys", sorted((first["rooms"][0].get("options") or {}).keys()))

    # 4) parsed candidates via the real code path
    candidates = source.check(hotel_id, check_in, check_out, 2, 0)
    _dump("check() candidates", [c.__dict__ for c in candidates])

    # 5) assumption checklist
    room = first.get("rooms", [{}])[0] if first.get("rooms") else {}
    print("\n=== ASSUMPTION CHECK ===")
    print(f"1. hotelId search param      : {'OK' if search_id else 'FAIL'} (searchId={search_id})")
    print(f"2. poll returned rooms        : {'OK' if room else 'FAIL'} (after {polls} polls)")
    print(f"3. field: total               : {'OK' if 'total' in room else 'MISSING'}")
    print(f"   field: agencyName          : {'OK' if 'agencyName' in room else 'MISSING'}")
    print(f"   field: fullBookingURL      : {'OK' if 'fullBookingURL' in room else 'MISSING'}")
    print(f"   field: options.refundable  : {'OK' if 'refundable' in (room.get('options') or {}) else 'MISSING'}")
    print(f"   field: options.breakfast   : {'OK' if 'breakfast' in (room.get('options') or {}) else 'MISSING'}")
    print(f"\nParsed {len(candidates)} candidate(s); "
          f"{sum(1 for c in candidates if c.refundable)} refundable.")


if __name__ == "__main__":
    main()
