# Price source migration — replacing Hotellook (§9)

**Status:** planning only. No implementation in this document — it exists to pick a
replacement aggregator and size the work.

**Author's note:** commercial terms, onboarding time, and exact field names below
are from public docs and provider marketing as of 2026-08; confirm each with the
provider before committing. The *technical* findings about Hotellook are verified
against the live service (see below).

---

## 1. Why we're here

The `hotellook` price source is built on the **Travelpayouts / Hotellook Data API**.
That API is gone:

- Travelpayouts **permanently closed Hotellook on 20 Oct 2025** — the brand, the
  affiliate program, and the Data API. Bookings made before that date were still
  tracked/paid; revenue-generating deep-links now redirect to Booking.com.
- Live probes on **2026-08-02** (from a network that reaches the host) confirm the
  API is fully removed, not merely moved:

  | Request | Result |
  |---|---|
  | `GET /api/v2/lookup.json?query=Amrita…` | HTTP **404** (nginx) |
  | `GET /api/v2/search/start.json` | HTTP **404** (nginx) |
  | `GET /` (host root) | HTTP **404** (nginx) |
  | `http://…/api/v2/lookup.json` | 301 → same dead HTTPS path |

  The 404s are origin-level nginx errors behind CloudFront — every route under
  `engine.hotellook.com/api/v2/*` is unrouted. `resolve_hotel()` raises on the
  lookup 404; `check()` swallows the search 404s and returns `[]`, i.e. "no drop,
  ever" — silent failure in production.

None of the three assumptions `scripts/validate_hotellook.py` was written to check
(the `hotelId` search param, poll-until-complete, response field names) can be
validated: there is no live service to validate against.

## 2. What a replacement must provide

Driven by the interface (`app/services/price_source/base.py`) and the spec:

| Need | Where it's used | Required field(s) |
|---|---|---|
| Hotel lookup by **name + city** → provider hotel ID | §6b, `resolve_hotel()` | ranked matches w/ id, name, city, (lat/lng) |
| **Per-room rates** for a stay (dates + occupancy) | §7/§8, `check()` | `total_price`, `currency` |
| **Refundable / free-cancellation** flag per rate | §7 rule 3 (correctness *is* ethics) | boolean (or cancellation policy → boolean) |
| **Board type** (RO/BB/HB/FB) | §7 rule 2 (like-for-like) | meal plan |
| **Rebook link** | §9 economics | affiliate deep-link *or* an API book flow |
| **Commission-friendly economics** | §9 | commission-on-booking, or margin, not per-call fees |
| EUR pricing, EU/GDPR posture | LV-based operator | — |

Spec §9 also says: **avoid unofficial RapidAPI scrapers** (ToS risk, fragility),
and beware **free tiers that don't scale** (e.g. Amadeus ~2k calls/month ≈ 30
bookings at 2 checks/day).

## 3. The models diverge — pick the model first

Hotellook was **metasearch + affiliate deep-link**: read cross-OTA rates, send the
user to an OTA via an affiliate link, earn commission on their booking. We never
touched the transaction. Post-Hotellook, that exact shape is scarce. The realistic
successors fall into two models:

- **A. Affiliate / metasearch (keep our economics).** Read rates, hand off via a
  commission deep-link. No merchant liability, no payment/cancellation handling.
  Closest to today's design, but cross-OTA structured *data* APIs (vs. link/widget
  builders) are now largely single-OTA.
- **B. B2B booking API (richer data, heavier).** Net/retail rates with full
  refundable + cancellation-policy detail and meal plans; you book *through* the API
  and earn margin. Better data for drop-detection, but you become the agent/merchant
  (payments, cancellations, support). The "rebook link" becomes an in-app book flow,
  which is **new surface area beyond the current read-only interface**.

Our monitoring loop only *reads* availability, so either model maps onto `check()`.
The divergence is in **how the user rebooks** and **who owns the transaction**.

## 4. Candidates

| Provider | Model | Cross-OTA? | Refundable + board | Rebook | Onboarding | Notes |
|---|---|---|---|---|---|---|
| **Booking.com Demand API** (direct partner, or via Travelpayouts) | A (commission) | No (single OTA) | Yes — cancellation policies + meal plans | Affiliate deep-link | Approval-gated | 3M+ properties; preserves our economics; single-OTA is usually fine since we track *one* booked property. |
| **Travelpayouts network** (Booking/WayAway/Trivago offers) | A (commission) | Mixed | Varies by offer | Deep-links/widgets | Easy (already have an account) | Still operating (Go Travel Un Limited). Mostly **links/widgets**, not a structured per-hotel price *data* API — may not meet `check()` needs on its own. |
| **RateHawk / Emerging Travel Group (ETG) API v3** | B (margin) | Wholesale (many suppliers) | Yes — detailed cancellation policies + room/board | API book flow | Contract + deposit | Established, deep global inventory, static content + hotel mapping. Heaviest but most capable. |
| **LiteAPI** | B (margin/markup) | Wholesale | Yes — `refundableTag` RFN/NRFN + policies | API prebook/book | **Self-serve, instant sandbox key** | Developer-first; lowest friction of the booking-API group; good prototype→prod path. |
| **Amadeus Self-Service Hotel API** | Per-transaction | Limited | Yes | No affiliate link | Easy signup | **Prototype-only** — the free-tier "cost trap" §9 explicitly warns about; no commission/deep-link. |
| RapidAPI scraper APIs | — | — | — | — | — | **Excluded by §9.** |

## 5. Recommendation

Two-track, decided by whether we keep the affiliate model or move to booking:

1. **Preferred (keep §9 economics): Booking.com Demand API**, direct partner or via
   Travelpayouts. It keeps commission-on-rebook + deep-links (no merchant burden),
   and gives cancellation policies (→ refundable flag) and meal plans. The one gap
   vs. Hotellook is cross-OTA breadth — acceptable because we monitor a *specific*
   property the user already booked, so a strong single-OTA rate for that property
   and dates is enough for a like-for-like refundable comparison.
2. **Pragmatic fallback if partner approval is slow: LiteAPI.** Self-serve sandbox
   key today, clean refundable/board data, and it fits `check()` with an adapter.
   Accept the model shift to a book-through-us flow (new rebook surface, §11-ish),
   or use it read-only for detection while rebooking still hands off to an OTA.

Start a Booking.com partner/Demand API application now (the long pole), and build a
LiteAPI adapter in parallel against its sandbox to de-risk the interface and keep
detection testable end-to-end.

## 6. Interface impact (small — the interface holds up)

`PriceSource` (`resolve_hotel` + `check` → `RateCandidate`) is provider-agnostic and
mostly survives. Per-provider adapter work:

- **New module** `app/services/price_source/<provider>.py` implementing `PriceSource`;
  wire it into the factory alongside `mock`/`hotellook`, selected by `PRICE_SOURCE`.
- **Auth:** replace Hotellook's md5 `_signature` with the provider's scheme
  (API key / Basic / OAuth). New settings in `app/config.py` + `.env.example`
  (e.g. `PRICE_SOURCE=booking|liteapi`, provider key/secret) — mirror the existing
  `travelpayouts_*` pattern.
- **`resolve_hotel`:** map the provider's hotel-search/autocomplete (and, for
  booking APIs, its static-content/mapping endpoint) into `HotelMatch`, keeping the
  single-match→0.95 / multi→0.5 confidence rule (§6b).
- **`check`:** map availability/room results into `RateCandidate`. Sync vs. async
  (poll) differs per provider; the current MAX_POLLS/interval pattern generalizes.
- **`refundable`:** derive from the provider's cancellation policy (RateHawk/LiteAPI
  give explicit refundable tags; Booking gives policy objects → boolean).
- **`board_type`:** most replacements expose a **real meal plan** (RO/BB/HB/FB),
  which *removes* Hotellook's breakfast-only limitation noted in code (§7).
- **`deep_link`:** affiliate URL (model A) **or** a signal to start an in-app book
  flow (model B) — the latter is the only genuinely new surface beyond today's
  read-only design and should be scoped with §11 (Stripe/checkout).
- **Tests:** `tests/test_hotellook.py` becomes a template — mock the HTTP client,
  pin any signature/auth to the provider's documented vector, assert the room→
  candidate mapping (refundable, board, deep-link).

Keep `hotellook.py` and `test_hotellook.py` as the reference example; mark the
source defunct (done) rather than deleting, so the mapping logic stays legible.

## 7. Open decisions for the team

1. **Affiliate (A) or booking (B)?** This is the business call that picks the
   provider and decides whether "rebook" stays a redirect or becomes an in-app flow.
2. Is **single-OTA** coverage (Booking) acceptable given we monitor one known
   property, or is cross-OTA breadth a hard requirement?
3. Appetite for **merchant/agent obligations** (payments, cancellations, support)
   that model B brings?

---

### Sources

- [FAQ on the closure of Hotellook — Travelpayouts Help Center](https://support.travelpayouts.com/hc/en-us/articles/29534131568530-FAQ-on-the-closure-of-Hotellook)
- [Travelpayouts — affiliate network / programs](https://www.travelpayouts.com/about/)
- [Booking.com Affiliate Partner Program teardown (2026)](https://track360.io/blog/booking-com-affiliate-partner-program-operator-teardown-2026)
- [Booking.com Demand API — cancellation policies](https://developers.booking.com/demand/docs/orders-api/cancellation-policies)
- [RateHawk / ETG API](https://www.ratehawk.com/lp/en-us/API/)
- [LiteAPI — hotel rates JSON structure & refundable tags](https://docs.liteapi.travel/docs/hotel-rates-api-json-data-structure)
- Live probe results: `scripts/validate_hotellook.py` run + `curl -i` against
  `engine.hotellook.com/api/v2/*` on 2026-08-02 (all 404).
