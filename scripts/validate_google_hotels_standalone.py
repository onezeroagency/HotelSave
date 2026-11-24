#!/usr/bin/env python3
"""Standalone live diagnostic for the Google Hotels (SerpApi) watch engine, v3.

ZERO dependencies — system python3 is enough. Spends ~7 searches (free tier:
250/month) and NEVER prints the key.

    SERPAPI_KEY=$(cat ~/Desktop/serpapi_key.txt) python3 validate_google_hotels_standalone.py \
        "Grand Poet Hotel and SPA by Semarah" "Riga" LV 2026-12-10 2026-12-12

v3 exists because run #2 found the free_cancellation filter is honored
(echoed) but returned ZERO rates for the Riga hotel, whose unfiltered panel
was already thin (1 OTA, no cancellation flags). This run distinguishes:
  world 1 — thin panels are a small-market thing (the Rome control is rich,
            flags present, filter returns rates there)
  world 2 — the filter is broken on details requests everywhere (unfiltered
            panels show flags, filtered come back empty for Rome too)
  world 3 — cancellation data is sparse everywhere (no flags anywhere)

Paste the whole output back into the Claude session.
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

GENERIC = {"hotel", "hotels", "the", "and", "by", "spa", "resort", "resorts",
           "inn", "suites", "apartments", "aparthotel", "boutique",
           "guesthouse", "hostel"}


def out(*parts) -> None:
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
        out("  HTTP", e.code, "from SerpApi:", e.read().decode("utf-8", "replace")[:300])
        return None
    except Exception as e:  # noqa: BLE001 - one-shot diagnostic script
        out("  Request failed:", repr(e))
        return None
    if payload.get("error"):
        out("  SerpApi error:", payload["error"])
        return None
    return payload


def tokens(n: str) -> set:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", n.strip().lower()) if t and t not in GENERIC}


def agrees(booked: str, cand: str) -> bool:  # sync: adapter _names_agree
    bt, ct = tokens(booked), tokens(cand)
    if bt and ct:
        shared = bt & ct
        if len(shared) >= 2 and len(shared) / len(bt) >= 0.6:
            return True
        if len(bt) == 1 and bt <= ct:
            return True
    return SequenceMatcher(None, booked.lower(), cand.lower()).ratio() >= 0.75


def usable_total(block: dict | None):
    if not block:
        return None
    for key in ("extracted_after_taxes_fees", "extracted_lowest"):
        if block.get(key) is not None:
            return block[key]
    return None


def analyze(payload: dict, label: str) -> None:
    prices = payload.get("prices") or []
    rooms = [
        (b.get("source"), r)
        for b in (payload.get("featured_prices") or [])
        for r in (b.get("rooms") or [])
    ]
    echo = (payload.get("search_parameters") or {}).get("free_cancellation")
    fc_prices = sum(1 for e in prices if e.get("free_cancellation") is True)
    fc_rooms = 0
    for _, r in rooms:
        rates = [x for x in (r.get("rates") or []) if isinstance(x, dict)]
        if r.get("free_cancellation") is True or any(
            x.get("free_cancellation") is True for x in rates
        ):
            fc_rooms += 1
    totals = sum(1 for e in prices if usable_total(e.get("total_rate"))) + sum(
        1 for _, r in rooms if usable_total(r.get("total_rate"))
    )
    srcs = ", ".join(sorted({str(e.get("source")) for e in prices})[:6]) or "-"
    out(f"  [{label}] prices={len(prices)} rooms={len(rooms)} usable_totals={totals} "
        f"fc_flags: prices={fc_prices} rooms={fc_rooms}  echo={echo!r}")
    out(f"           OTA sources: {srcs}")
    cheapest = None
    for e in prices:
        t = usable_total(e.get("total_rate"))
        if t is not None and (cheapest is None or t < cheapest[0]):
            cheapest = (t, e.get("source"))
    if cheapest:
        out(f"           cheapest usable OTA total: EUR {cheapest[0]} ({cheapest[1]})")


def probe(name: str, city: str, gl: str, check_in: str, check_out: str, variants) -> None:
    out(f"\n=== {name!r} / {city} / {check_in} → {check_out}")
    payload = get({"q": f"{name} {city}", "gl": gl,
                   "check_in_date": check_in, "check_out_date": check_out})
    if payload is None:
        return
    props = payload.get("properties") or []
    chosen = next(
        (p for p in props[:5] if agrees(name, p.get("name") or "") and p.get("property_token")),
        None,
    )
    for p in props[:4]:
        out(f"  {'MATCH ' if agrees(name, p.get('name') or '') else '      '}{p.get('name')!r}")
    if not chosen:
        chosen = props[0] if props and props[0].get("property_token") else None
        out("  (no name agreement — probing the top hit anyway)")
    if not chosen:
        out("  no property_token at all — cannot probe details")
        return
    base = {"property_token": chosen["property_token"], "check_in_date": check_in,
            "check_out_date": check_out, "adults": 2, "children": 0}
    for label, extra in variants:
        detail = get({**base, **extra})
        if detail is not None:
            analyze(detail, label)


def main() -> int:
    if not KEY:
        out("Set SERPAPI_KEY first (see the command in the file header).")
        return 2
    name = sys.argv[1] if len(sys.argv) > 1 else "Grand Poet Hotel and SPA by Semarah"
    city = sys.argv[2] if len(sys.argv) > 2 else "Riga"
    gl = (sys.argv[3] if len(sys.argv) > 3 else "LV").lower()
    check_in = sys.argv[4] if len(sys.argv) > 4 else (date.today() + timedelta(days=30)).isoformat()
    check_out = sys.argv[5] if len(sys.argv) > 5 else (
        date.fromisoformat(check_in) + timedelta(days=2)
    ).isoformat()

    out("YOUR HOTEL — three detail variants:")
    probe(name, city, gl, check_in, check_out, [
        ("unfiltered        ", {}),
        ("unfiltered, gl=lv ", {"gl": "lv"}),
        ("fc-filter         ", {"free_cancellation": "true"}),
    ])

    out("\nCONTROL (big market) — same request shapes:")
    probe("Hotel Artemide", "Rome", "it", check_in, check_out, [
        ("unfiltered        ", {}),
        ("fc-filter         ", {"free_cancellation": "true"}),
    ])

    out("\nDone — paste ALL of the above back into the Claude session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
