#!/usr/bin/env python3
"""Standalone live validation for the Google Hotels (SerpApi) watch engine.

ZERO dependencies — macOS/Linux system python3 is enough. No repo install, no
pip, no venv. It exists because the person running it may have nothing but
this one file and a SerpApi key.

    SERPAPI_KEY=$(cat ~/Desktop/serpapi_key.txt) python3 validate_google_hotels_standalone.py \
        "Grand Poet Hotel and SPA by Semarah" "Riga" LV 2026-12-10 2026-12-12

It spends 2 searches (free tier: 250/month) and NEVER prints the key — all
output is scrubbed before printing.

What it checks (mirrors app/services/price_source/google_hotels.py — keep the
two in sync):
  1. search → does the top property match the hotel by name? (auto-resolve)
  2. details → money fields: are tax-INCLUSIVE totals present
     (extracted_after_taxes_fees / extracted_lowest)? Entries with only
     pre-tax figures are skipped by the adapter, by design.
  3. free_cancellation flags: present, and on which level?
  4. room names / breakfast signals in featured_prices.

Paste the whole output back into the Claude session; any mismatch against the
adapter is a one-line fix there.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from difflib import SequenceMatcher

BASE = os.environ.get("SERPAPI_BASE", "https://serpapi.com/search")
KEY = os.environ.get("SERPAPI_KEY", "")


def out(*parts) -> None:
    """Print with the key scrubbed, wherever it might appear (URLs in error
    bodies included)."""
    text = " ".join(str(p) for p in parts)
    if KEY:
        text = text.replace(KEY, "***KEY***")
    print(text)


def get(params: dict) -> dict | None:
    query = urllib.parse.urlencode(
        {"engine": "google_hotels", "currency": "EUR", "hl": "en", "api_key": KEY, **params}
    )
    try:
        with urllib.request.urlopen(f"{BASE}?{query}", timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        out("HTTP", e.code, "from SerpApi:", e.read().decode("utf-8", "replace")[:500])
        return None
    except Exception as e:  # noqa: BLE001 - one-shot diagnostic script
        out("Request failed:", repr(e))
        return None
    if payload.get("error"):
        out("SerpApi error:", payload["error"])
        return None
    return payload


def total_from(block: dict | None) -> tuple[str, float] | None:
    """(which_field, value) by the adapter's rule: after-tax, else lowest,
    else None (pre-tax only → the adapter SKIPS, never under-prices)."""
    if not block:
        return None
    for key in ("extracted_after_taxes_fees", "extracted_lowest"):
        if block.get(key) is not None:
            return key, block[key]
    return None


def main() -> int:
    if not KEY:
        out("Set SERPAPI_KEY first, e.g.:")
        out('  SERPAPI_KEY=$(cat ~/Desktop/serpapi_key.txt) python3', sys.argv[0], "…")
        return 2

    name = sys.argv[1] if len(sys.argv) > 1 else "Grand Poet Hotel and SPA by Semarah"
    city = sys.argv[2] if len(sys.argv) > 2 else "Riga"
    country = (sys.argv[3] if len(sys.argv) > 3 else "LV").lower()
    check_in = sys.argv[4] if len(sys.argv) > 4 else (date.today() + timedelta(days=30)).isoformat()
    check_out = sys.argv[5] if len(sys.argv) > 5 else (
        date.fromisoformat(check_in) + timedelta(days=2)
    ).isoformat()

    out(f"\n=== 1. search: {name!r} / {city} / {country} / {check_in} → {check_out}")
    payload = get({
        "q": f"{name} {city}",
        "gl": country,
        "check_in_date": check_in,
        "check_out_date": check_out,
    })
    if payload is None:
        return 1
    props = payload.get("properties") or []
    if not props:
        out("NO properties[] in the response. Top-level keys were:", sorted(payload)[:15])
        return 1
    for p in props[:5]:
        sim = SequenceMatcher(None, (p.get("name") or "").lower(), name.lower()).ratio()
        out(f"  sim={sim:.2f}  {p.get('name')!r}  token={'yes' if p.get('property_token') else 'MISSING'}")
    top = props[0]
    top_sim = SequenceMatcher(None, (top.get("name") or "").lower(), name.lower()).ratio()
    out(f"  → top hit would {'AUTO-RESOLVE' if top_sim >= 0.75 else 'go to ask-the-user (sim < 0.75)'}")
    token = top.get("property_token")
    if not token:
        out("  Top hit has no property_token — adapter cannot proceed; paste this output back.")
        return 1

    out(f"\n=== 2. details for the top hit, 2 adults")
    payload = get({
        "property_token": token,
        "check_in_date": check_in,
        "check_out_date": check_out,
        "adults": 2,
        "children": 0,
    })
    if payload is None:
        return 1

    prices = payload.get("prices") or []
    featured = payload.get("featured_prices") or []
    out(f"  prices[]: {len(prices)} entries   featured_prices[]: {len(featured)} blocks")

    kept = skipped = refundable = 0
    out("\n  --- OTA-level entries (first 8, raw money fields) ---")
    for e in prices[:8]:
        t = total_from(e.get("total_rate"))
        raw = {k: v for k, v in (e.get("total_rate") or {}).items() if "extracted" in k}
        fc = {k: e[k] for k in ("free_cancellation", "free_cancellation_until_date") if k in e}
        out(f"  {e.get('source','?'):<20.20} {raw}  {fc}  → "
            + (f"USE {t[0]}={t[1]}" if t else "SKIP (pre-tax only)"))
    for e in prices:
        t = total_from(e.get("total_rate"))
        kept += bool(t)
        skipped += not t
        refundable += e.get("free_cancellation") is True

    named = boarded = 0
    out("\n  --- room-level entries (first 6) ---")
    shown = 0
    for block in featured:
        for room in block.get("rooms") or []:
            rates = [r for r in (room.get("rates") or []) if isinstance(r, dict)]
            breakfast = any(r.get("breakfast_included") for r in rates) or any(
                "breakfast" in str(i).lower() for r in rates for i in (r.get("inclusions") or [])
            ) or "breakfast" in (room.get("name") or "").lower()
            t = total_from(room.get("total_rate"))
            named += bool(room.get("name"))
            boarded += breakfast
            if shown < 6:
                out(f"  {block.get('source','?'):<14.14} {room.get('name')!r:<42.42} "
                    f"board={'BB' if breakfast else '?'}  "
                    + (f"{t[0]}={t[1]}" if t else "SKIP (pre-tax only)"))
                shown += 1

    out("\n=== 3. verdict inputs")
    out(f"  usable totals:        {kept}/{len(prices)} OTA entries  ({skipped} would be skipped)")
    out(f"  refundable-flagged:   {refundable}/{len(prices)}")
    out(f"  named rooms seen:     {named}   breakfast-signalled: {boarded}")
    if prices and not refundable:
        out("  ⚠ no free_cancellation flags — §7's refundable rule would match nothing here")
    if prices and not kept:
        out("  ⚠ every entry pre-tax only — the money-field mapping needs a fix, not the data")
    out("\nPaste ALL of the above back into the Claude session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
