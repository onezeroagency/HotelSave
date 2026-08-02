# HotelSave

Hotel price-drop **rebooking** micro-SaaS. We watch a user's specific refundable
booking, compare only **like-for-like** refundable rates, and **guard the
cancellation deadline** — so a saving never slips past the window where they can
still act on it.

> Positioning: not "check hotel prices" (every free tool does that). We watch
> *your* booking and guard *your* deadline. That deadline-guard is the paid wedge.

This repo is **build-sequence step 1 — the skeleton** ([`spec §13`](docs/spec.md)):
DB schema, `MonitoringJob` CRUD, auth, Stripe wiring, the `PriceSource`
interface, and the deadline-clocked scheduler loop. Ingestion (forward-to email
+ LLM parse), the real aggregator, and Klaviyo flows are the next steps.

## Architecture

```
Inbound booking ─▶ create MonitoringJob ─▶ deadline-clocked scheduler
                                                     │
                                          PriceSource.check() (aggregator)
                                                     │
                                          like-for-like comparison (§7)
                                              ╱                ╲
                                       drop found        deadline near, no drop
                                              ╲                ╱
                                         emit event ─▶ Klaviyo (flows → email)
```

**Principle: backend detects, Klaviyo delivers.** The backend never sends email
directly — it emits events; Klaviyo flows own all copy, timing, and lifecycle.

## Layout

| Path | Role |
|---|---|
| `app/models.py` | `User`, `MonitoringJob`, `PriceCheck` — the whole system is one object with a state machine around it (§3–4) |
| `app/routers/auth.py` | register / login / me (JWT) |
| `app/routers/jobs.py` | `MonitoringJob` CRUD, scoped to the user |
| `app/routers/billing.py` | Stripe checkout + webhook → `plan_status` (§11) |
| `app/routers/inbound.py` | forward-to email webhook (Postmark) → ingestion (§5) |
| `app/services/parser/` | **one LLM call → structured booking** (§6a) — `mock` for dev/tests, Claude in prod, swappable |
| `app/services/email_inbound/` | inbound-email provider adapters (Postmark today), behind a common shape (§5) |
| `app/services/ingestion.py` | email → parse → resolve hotel_id → job, with the §6 post-parse guards |
| `app/services/price_source/` | **the aggregator lives behind this interface** (§9) — `mock`, or `hotellook` (Travelpayouts) via `PRICE_SOURCE` |
| `app/services/matching.py` | like-for-like rules + drop floor `max(€10, 3%)` (§7) |
| `app/services/klaviyo.py` | the events the backend emits (§10) |
| `app/scheduler/worker.py` | deadline-clocked polling loop (§8) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # tweak if you like; SQLite works out of the box

uvicorn app.main:app --reload   # API at http://localhost:8000/docs
python -m app.scheduler.worker  # one scheduler pass over due jobs (--loop to keep running)
pytest                          # 16 tests, all green
```

Interactive API docs (Swagger) live at `/docs`. On SQLite the tables are
auto-created at startup for zero-config dev.

### End-to-end by hand

```bash
# register + login
curl -s localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"password123"}'
TOKEN=$(curl -s localhost:8000/auth/login \
  -d 'username=me@example.com&password=password123' | jq -r .access_token)

# create a monitored booking
curl -s localhost:8000/jobs -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "hotel_name_raw":"Hotel Kaunas","city":"Vilnius",
    "check_in":"2026-09-10","check_out":"2026-09-13",
    "board_type":"BB","adults":2,"children":0,
    "original_price":"420.00","currency":"EUR","refundable":true,
    "cancellation_deadline":"2026-09-07T23:59:00"}'

# run the scheduler — mock source reports a lower rate → "Price Drop Found" event
python -m app.scheduler.worker
```

With no `KLAVIYO_API_KEY` set, events are logged (dry-run) instead of sent, so
the whole detect→alert path is exercisable without any external account.

## Configuration

All via env / `.env` (see `.env.example`). Nothing external is required to run
locally: Stripe and Klaviyo degrade to clear errors / dry-run logging until you
add real keys. The price source defaults to `mock`.

## Database migrations

SQLite dev auto-creates tables. For Postgres/production use Alembic:

```bash
export DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/hotelsave
alembic upgrade head
```

## Ingestion (§5, §6)

Forward a confirmation to the inbound address; the provider POSTs it to
`/inbound/postmark`. The sender is matched to a user, one LLM call extracts the
booking, the hotel is resolved to an aggregator ID, and a `MonitoringJob` is
created — `active` if everything's present, otherwise a `pending_*` state with a
prompt back to the user (non-refundable, missing deadline, or ambiguous hotel).
With `PARSER=mock` (the default) the whole path runs with no API key:

```bash
# after registering me@example.com, forward a booking to it:
curl -s localhost:8000/inbound/postmark -H 'content-type: application/json' -d '{
  "FromFull": {"Email": "me@example.com"},
  "TextBody": "Hotel: Grand Vilnius\nCity: Vilnius\nCheck-in: 2026-09-10\nCheck-out: 2026-09-13\nBoard: BB\nAdults: 2\nTotal: 420\nCurrency: EUR\nRefundable: Yes\nCancellation: 2026-09-07T23:59"}'
# → {"status": "monitoring", "job_id": 1}
```

## Price source — Travelpayouts / Hotellook (§9)

Set `PRICE_SOURCE=hotellook` with a `TRAVELPAYOUTS_TOKEN` + `TRAVELPAYOUTS_MARKER`
to run against the real aggregator. `resolve_hotel` uses the `lookup.json`
endpoint; `check` runs the async signed search (`search/start` → poll
`search/getResult`) and maps each room offer to a like-for-like candidate,
carrying the OTA, total, free-cancellation flag, and the marker-tagged rebook
deep-link. The md5 request signature is pinned to Travelpayouts' documented test
vector in `tests/test_hotellook.py`.

> **Live validation still pending.** The request shapes are built from the API
> docs but haven't been run against the live service (this build environment's
> network policy blocks `engine.hotellook.com`). First live run should confirm
> the search hotel-id param, the poll-until-complete termination, and board-type
> derivation (the API exposes only a breakfast boolean, so HB/FB aren't
> distinguished). Run it locally, or from an environment whose network policy
> allows `engine.hotellook.com`.

## Alerting — Klaviyo (§10)

Backend detects, Klaviyo delivers. The backend already emits the events
(`app/services/klaviyo.py`); the Klaviyo-side setup — metrics, Flow 1 (Price
Drop) & Flow 2 (Deadline Guard) copy, and the transactional-send config — lives
in [`docs/klaviyo-setup.md`](docs/klaviyo-setup.md). Bootstrap the metrics into a
**HotelSave-specific** Klaviyo account with:

```bash
KLAVIYO_API_KEY=pk_... python scripts/bootstrap_klaviyo_metrics.py you@example.com
```

## What's next (spec §13)

5. **Launch narrow** — one traveler niche, first-booking-free funnel.

✅ Step 1 (skeleton), Step 2 (ingestion), Step 3 (real price source, pending live
validation), and Step 4 (alerting: backend events done; flows documented as
deployable config) are in.
