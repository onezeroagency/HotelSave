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
| `app/services/price_source/` | **the aggregator lives behind this interface** (§9) — `mock` today, real source is a one-line swap |
| `app/services/matching.py` | like-for-like rules + drop floor `max(€10, 3%)` (§7) |
| `app/services/klaviyo.py` | the four events the backend emits (§10) |
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

## What's next (spec §13)

2. **Ingestion** — forward-to email → LLM parse → hotel-ID resolution → job.
3. **Monitoring** — real aggregator behind `PriceSource`; watch it detect a real drop.
4. **Alerting** — wire the four Klaviyo events into Flows 1 (Price Drop) & 2 (Deadline Guard).
5. **Launch narrow** — one traveler niche, first-booking-free funnel.
