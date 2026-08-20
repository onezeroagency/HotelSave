# Price source migration — replacing Hotellook (§9)

**Status:** planning only. No implementation in this document — it exists to pick a
replacement aggregator and size the work.

**Decision (2026-08-02):** proceed with the **affiliate model** via the
**Booking.com Demand API** — it preserves §9's economics (commission-on-rebook,
no per-call fees) *and* exposes the availability data the detection loop needs.
Concrete onboarding + integration path in [§8](#8-chosen-path-affiliate-via-bookingcom-demand-api).
No adapter code will be written until it can be validated against Booking's
sandbox with a real key — writing an integration from docs alone is exactly what
produced the dead Hotellook code.

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

1. ~~**Affiliate (A) or booking (B)?**~~ **Decided (2026-08-02): affiliate (A).**
   Rebook stays a commission deep-link; no merchant/agent obligations.
2. **Single-OTA coverage accepted.** We monitor one already-booked property, so a
   strong Booking.com rate for that property/dates is enough for a like-for-like
   refundable comparison. Cross-OTA breadth is a nice-to-have, not a blocker.
3. N/A under model A — no payments/cancellations/support burden.

## 8. Chosen path: affiliate via Booking.com Demand API

**Why this clears the affiliate model's data problem.** Affiliate programs usually
hand you deep-links, not a pollable price API. Booking.com's **Demand API** is the
exception: the **Demand API** exposes both a **check-availability** endpoint
(structured rates for detection, §7/§8) *and* affiliate attribution on the rebook
link — the same combination Hotellook uniquely provided, and no per-call fee. The
catch (see onboarding below): the Demand API is a **separate** technical onboarding
from the regional affiliate signup (which, for us, runs through CJ), and API access
can be gated for new partners — so treat "can we actually get the Demand API?" as an
open risk, not a given.

### Onboarding — two SEPARATE Booking tracks; you need both, don't conflate them

1. **Affiliate program → deep-links + commission (the rebook payout).**
   In the EU / Eastern Europe (incl. Latvia) Booking administers this **through CJ
   (Commission Junction)**: `partnerships.booking.com` → *Register* → the Partner
   Centre sign-up routes you to **"Register with CJ"** for these markets. That is
   the *correct* front door for our region — CJ **is** Booking's affiliate network
   here (this varies by market; earlier notes wrongly said Booking wasn't on CJ).
   It grants tracked deep-links + commission — **not** the availability API.
2. **Demand API → the availability/price feed (the detection loop, §7/§8).**
   A **separate** onboarding at **`developers.booking.com`**: an affiliate/technical
   partner requests API access and receives an API key + `X-Affiliate-Id`. Being a
   CJ-managed affiliate does **not** automatically grant it, and it can be gated for
   brand-new partners. **This is the real technical dependency — confirm it's
   attainable early**, before assuming the product can be built on Booking data.

⚠️ **CJ ≠ Demand API.** CJ (and CJ's own link/product API) gives affiliate *links*,
never hotel *availability*. If the Demand API can't be secured, detection falls back
to a self-serve data API (LiteAPI/RateHawk) while the CJ Booking deep-links still
carry the rebook commission — the "split" in §3, accepting that detected vs. rebook
inventory can differ (soften alert copy: "prices have dropped — check your rebook
option" rather than promising an exact figure).

The affiliate signup needs OneZero's business details **and a live website** (see
`landing/` — reviewers reject applications with no working site); no code unblocks it.

### API facts (from Booking.com docs — to be re-verified against the sandbox)

| Thing | Value |
|---|---|
| Sandbox base URL | `https://demandapi-sandbox.booking.com/3.1` |
| Production base URL | `https://demandapi.booking.com/3.1` |
| Auth headers | `X-Affiliate-Id: <id>` + `Authorization: Bearer <token>` |
| Transport | REST, JSON, **POST** requests |
| Sandbox behaviour | Same creds as prod; simulates search/booking/payment/cancellation with **no real charges** — safe to validate against |
| Detection endpoint | Accommodations **availability** (e.g. bulk/check-availability) |
| Lookup | Accommodations search/details (name + city → Booking property id) |

The sandbox is what lets us **validate before prod** — the safeguard the Hotellook
integration never had.

### Interface impact (a new adapter behind the existing `PriceSource`)

- **New module** `app/services/price_source/booking.py` implementing `PriceSource`;
  select via `PRICE_SOURCE=booking`. Add settings `booking_api_key`,
  `booking_affiliate_id`, `booking_env=sandbox|production` (mirror the existing
  `travelpayouts_*` block in `app/config.py` + `.env.example`).
- **Auth:** replace Hotellook's md5 `_signature` with the two headers above — much
  simpler; no signature vector to pin.
- **`resolve_hotel`:** Booking accommodations search by name + city → `HotelMatch`
  (keep the single-match 0.95 / multi 0.5 confidence rule, §6b).
- **`check`:** POST availability for property + dates + occupancy → map rooms to
  `RateCandidate`. `refundable` ← cancellation-policy field; `board_type` ← meal
  plan (RO/BB/HB/FB — *better* than Hotellook's breakfast-only bool); `deep_link` ←
  the affiliate-attributed property URL.
- **Validate every field name against the sandbox** before trusting it. This is the
  step that was skipped for Hotellook.
- **Tests:** reuse `tests/test_hotellook.py` as the template — mock the client,
  assert the room→candidate mapping; no signature test needed.

### Build sequence

1. ~~Scaffold `booking.py` + config + `.env.example` (auth/base URLs are known facts).~~
   **Done (2026-08-02):** `app/services/price_source/booking.py` (header auth +
   sandbox/prod base URLs, `resolve_hotel`/`check` stubbed with `NotImplementedError`),
   `booking_*` settings in `app/config.py` + `.env.example`, factory wiring for
   `PRICE_SOURCE=booking`, and `tests/test_booking.py` covering the plumbing. The
   endpoint mapping is intentionally left unimplemented until step 2.
2. **(needs the API key)** Point at the **sandbox**, fill in `resolve_hotel` + `check`,
   and validate end-to-end with a real property (the "Amrita Hotel Liepaja" run,
   redone against Booking) — confirm the actual field names as you go.
3. Confirm the like-for-like path (§7) and the scheduler (§8) fire on a simulated
   drop; then flip `booking_env` to production.

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
