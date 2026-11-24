"""Live validation for the Google Hotels (SerpApi) price source (§9).

The build environment cannot call serpapi.com, so the adapter was written from
SerpApi's published docs (google-hotels-api / google-hotels-property-details,
read 2026-11-24) but never run live — the same position the LiteAPI adapter
was in before scripts/validate_liteapi.py. Run this from any machine with
normal internet; the free tier (250 searches/month) is plenty. It spends 2
searches per run:

    SERPAPI_KEY=xxx python scripts/validate_google_hotels.py \
        "Grand Poet Hotel and SPA by Semarah" "Riga" LV 2026-12-10 2026-12-12

It confirms the assumptions the offline build couldn't, printing raw API
envelopes next to the parsed output so any mismatch is obvious:

  1. search params + properties[].property_token and name ranking
     (does the top hit auto-resolve the right hotel?)
  2. details response: prices[] money fields — ARE after-tax totals present?
     (entries with only pre-tax figures are skipped by design; if everything
     is being skipped, the money-field mapping needs the fix, not the data)
  3. free_cancellation flags: how often are they present, and on which level
     (prices[] vs featured_prices[].rooms[])?
  4. room names / breakfast signals in featured_prices

Paste the output back and any needed fix is a one-line change.
"""

import sys
from datetime import date, timedelta

from app.config import settings
from app.services.price_source.google_hotels import GoogleHotelsPriceSource


def main() -> int:
    if not settings.serpapi_key:
        print("Set SERPAPI_KEY (serpapi.com → Api Key). Free tier is enough.")
        return 2

    name = sys.argv[1] if len(sys.argv) > 1 else "Grand Poet Hotel and SPA by Semarah"
    city = sys.argv[2] if len(sys.argv) > 2 else "Riga"
    country = sys.argv[3] if len(sys.argv) > 3 else "LV"
    check_in = (
        date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else date.today() + timedelta(days=30)
    )
    check_out = (
        date.fromisoformat(sys.argv[5]) if len(sys.argv) > 5 else check_in + timedelta(days=2)
    )

    src = GoogleHotelsPriceSource()

    print(f"\n=== 1. resolve: {name!r} / {city} / {country} / {check_in}→{check_out}")
    matches = src.resolve_hotel(name, city, country=country, check_in=check_in, check_out=check_out)
    for m in matches:
        print(f"  conf={m.confidence:.2f}  {m.name!r}  token={m.hotel_id[:40]}…")
    if not matches:
        print("  NO MATCHES — check the raw 'properties' array manually at serpapi.com/playground")
        return 1
    if not (len(matches) == 1 and matches[0].confidence >= 0.9):
        print("  NOTE: would go to pending_hotel (§6b) — fine if the names genuinely differ")

    token = matches[0].hotel_id
    print(f"\n=== 2. check: token={token[:40]}… {check_in}→{check_out} 2 adults")
    candidates = src.check(token, check_in, check_out, 2, 0)
    print(f"  {len(candidates)} candidates parsed")
    refundable = [c for c in candidates if c.refundable]
    named = [c for c in candidates if c.room_name]
    boarded = [c for c in candidates if c.board_type]
    for c in sorted(candidates, key=lambda c: c.total_price)[:10]:
        print(
            f"  {c.currency} {c.total_price:>8}  rfn={'Y' if c.refundable else '?'}"
            f"  board={c.board_type or '?'}  {c.ota:<18.18}  {c.room_name or ''}"
        )
    print(f"\n  refundable-flagged: {len(refundable)}/{len(candidates)}")
    print(f"  room-named:         {len(named)}/{len(candidates)}")
    print(f"  board-known:        {len(boarded)}/{len(candidates)}")
    if candidates and not refundable:
        print(
            "  WARNING: no free_cancellation flags seen — the §7 refundable rule"
            " will match nothing from this response; check prices[] raw entries."
        )
    if not candidates:
        print(
            "  WARNING: zero candidates — either the response shape differs from"
            " the docs, or every entry had only pre-tax money fields (see the"
            " skip log lines above)."
        )
    print("\nIf the numbers above look like Google's own page for this hotel, the")
    print("swap is safe to enable: PRICE_SOURCE=google_hotels + SERPAPI_KEY on Render.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
