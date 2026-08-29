# Klaviyo setup (§10)

**Principle: backend detects, Klaviyo delivers.** The backend never sends email
directly — it emits events; Klaviyo flows own all copy, timing, and testing. The
backend side is already built (`app/services/klaviyo.py`): set `KLAVIYO_API_KEY`
and it pushes events to Klaviyo; leave it blank and it logs them (dry-run).

This doc is the Klaviyo-*side* config: the metrics, the two core flows, and the
transactional-send setting. It's account-agnostic on purpose — deploy it into a
**HotelSave-specific Klaviyo account**, not an unrelated brand's.

---

## 0. Prerequisites

1. A Klaviyo account **for HotelSave** (its own sender identity / domain).
2. A private API key with event + flow scopes → set it as `KLAVIYO_API_KEY`.

## 1. Create the metrics

Klaviyo metrics come into existence the first time an event with that name is
received — you can't build a flow trigger before the metric exists. Bootstrap all
of them at once:

```bash
KLAVIYO_API_KEY=pk_... python scripts/bootstrap_klaviyo_metrics.py you@example.com
```

That sends one sample of each event (to the address you pass — use your own).
After it runs, the metrics below appear under **Analytics → Metrics**.

### Events the backend emits

| Event (metric) | When | Properties |
|---|---|---|
| `Account Created` | user registers (`POST /auth/register`) | `signup_source`, `forward_to` |
| `Booking Monitored` | job created / activated | `hotel`, `city`, `check_in`, `check_out`, `original_price`, `currency`, `cancellation_deadline` |
| `Price Drop Found` | actionable like-for-like drop (§7) | `hotel`, `city`, `check_in`, `check_out`, `nights`, `board_type`, `adults`, `old_price`, `new_price`, `savings_amount`, `savings_pct`, `rebook_url`, `has_rebook_url`, `currency`, `cancellation_deadline` |
| `Deadline Approaching` | 48h pre-deadline, no drop standing | `hotel`, `city`, `check_in`, `check_out`, `cancellation_deadline`, `checks_done`, `lowest_seen_price`, `currency` |
| `Monitoring Ended` | deadline passed / checked out | `hotel`, `best_savings_seen`, `outcome` |
| `Forwarded Without Account` | unknown sender forwarded a booking | `subject` |
| `Refundable Confirmation Needed` | parsed booking isn't refundable | `hotel`, `check_in` |
| `Cancellation Deadline Needed` | refundable but no deadline parsed | `hotel` |
| `Hotel Resolution Needed` | ambiguous hotel match | `hotel`, `candidates` |

Property names here are the exact keys the backend sends — reference them in
flow templates as `{{ event.savings_amount }}`, `{{ event.hotel }}`, etc.

---

## 2. The transactional gotcha — configure first, by hand

These are **transactional** alerts about a thing the user asked you to watch, so
they must send **regardless of marketing-consent status**. On every email action
in the flows below, set the message to **transactional** (Klaviyo: the email
action's settings → "This is a transactional message" / smart-sending off).
Otherwise a user who unsubscribes from marketing silently stops getting the price
drops they're paying for.

> **This cannot be done over the API.** `POST /api/flows` and
> `PATCH /api/flow-actions/{id}` both *accept* `transactional: true` and return
> HTTP 200 — and then store `false`. Verified 2026-08-29 against all three
> MyRoomWatch flows. There is no error to catch, so **read the flow back and
> check the value rather than trusting the write**. Every flow this repo created
> is currently `transactional: false` and must be flipped in the Klaviyo UI
> before going live.

---

> **Ready-made templates:** paste-ready HTML for all three flows lives in
> [`docs/klaviyo-templates/`](klaviyo-templates/) — `price-drop.html`,
> `deadline-guard.html` and `welcome.html`. They brand-match the site, branch on
> `has_rebook_url`, and use only keys the backend actually emits. Kept in the
> repo so the copy is version-controlled: the live Klaviyo templates were created
> from these files and should be re-synced from here when the copy changes.

## 3. Flow 1 — Price Drop Alert  *(trigger: `Price Drop Found`)*

Immediate send. Subject leads with the number. One job: show the saving, the
rebook button, and the safe sequence.

- **Trigger:** metric `Price Drop Found`
- **Timing:** send immediately
- **Message type:** transactional

**Subject:** `Save €{{ event.savings_amount }} at {{ event.hotel }} — rebook now`

**Body:**

> Good news — the price on your exact room at **{{ event.hotel }}**
> ({{ event.city }}, {{ event.check_in }} → {{ event.check_out }}) dropped.
>
> - You paid: {{ event.currency }} {{ event.old_price }}
> - New rate (same room, same dates, free cancellation): **{{ event.currency }} {{ event.new_price }}**
> - You save: **{{ event.currency }} {{ event.savings_amount }} ({{ event.savings_pct }}%)**
>
> {% if event.has_rebook_url %}
> **[ Rebook at the lower rate → ]({{ event.rebook_url }})**
> {% else %}
> Search **{{ event.hotel }}** for {{ event.check_in }}–{{ event.check_out }},
> {{ event.adults }} guest(s), and pick the refundable rate at or below
> {{ event.currency }} {{ event.new_price }}.
> {% endif %}
>
> ⚠️ Do it in this order: **book the new rate first, confirm it, then cancel your
> original.** Never cancel first — you could lose the room.
>
> Your free-cancellation window closes **{{ event.cancellation_deadline }}**, so
> don't sit on it.

**Enhancements (build in Klaviyo):**
- Conditional split on `savings_amount` for stronger urgency styling on big wins.
- One 24h follow-up **only if** not clicked **and** deadline is near (add a time
  delay + a "clicked email?" conditional split + a `cancellation_deadline`
  filter).
- **`rebook_url` is null until an affiliate program is live.** Branch on
  `has_rebook_url` (as the body above does) so the CTA is never a dead link: with
  a deep-link it's a one-click rebook that also books the commission; without one
  the user gets precise instructions to rebook manually. Remove the conditional
  once the affiliate deep-links land.

---

## 4. Flow 2 — Deadline Guard  *(trigger: `Deadline Approaching`)*

The flow free tools don't have. The retention story: the product visibly working
even when there's no win.

- **Trigger:** metric `Deadline Approaching`
- **Timing:** send immediately
- **Message type:** transactional

**Subject:** `Still watching {{ event.hotel }} — your free-cancel window closes soon`

**Body:**

> Quick heads-up: your free-cancellation window at **{{ event.hotel }}** closes in
> about **48 hours** ({{ event.cancellation_deadline }}).
>
> We've checked {{ event.checks_done }} times on your stay at {{ event.city }}
> ({{ event.check_in }} → {{ event.check_out }}) and haven't found a lower
> like-for-like refundable rate yet{% if event.lowest_seen_price %} — the best we
> saw was {{ event.currency }} {{ event.lowest_seen_price }}{% endif %}. We'll keep
> watching right up until the window closes; if anything drops, you'll hear from
> us instantly.
>
> Either way, here are your booking details so nothing slips by. Safe travels.

On a service that produces a saving only ~40–50% of the time, this email is what
stops the other half from asking "why am I paying." It converts silence into
perceived protection.

---

## 4b. Flow 3 — Welcome / Activation  *(trigger: `Account Created`)*

Signup is not activation. An account with no forwarded booking produces nothing —
no checks, no alerts, no reason to come back — so this email has exactly one job:
get the forwarding address in front of the user while they're still in the tab
they signed up from.

- **Trigger:** metric `Account Created`
- **Timing:** send immediately
- **Message type:** transactional (a service instruction, not a newsletter)
- **Template:** `docs/klaviyo-templates/welcome.html`

**Subject:** `You're in — now forward your first booking`

The forwarding address comes from the event (`{{ event.forward_to }}`) rather
than being hardcoded, so changing the inbound address in the backend changes the
email too. The template defaults it to `save@myroomwatch.com` if the property is
ever missing.

**Worth adding once there's signup volume:** a time-delay branch that re-prompts
after ~48h *only if* no `Booking Monitored` event has arrived for that profile.
That's the whole activation funnel — nothing else in the product matters until a
booking is forwarded.

---

## 5. Onboarding / lifecycle flows (optional, v1.1)

The ingestion events power light lifecycle flows (§5–§6):

- `Forwarded Without Account` → "You forwarded a booking — create an account to
  start watching it" (acquisition hook).
- `Refundable Confirmation Needed` → "We can only watch refundable bookings — is
  this one refundable? Reply with the cancellation date." (also teaches booking
  refundable next time).
- `Cancellation Deadline Needed` → "What's the free-cancellation date for
  {{ event.hotel }}?"
- `Hotel Resolution Needed` → "Which of these is your hotel?" pick-list from
  `{{ event.candidates }}`.
- `Monitoring Ended` → trend/retention wrap-up using `best_savings_seen`.

---

## 6. What's live in the MyRoomWatch account (`Uyzstd`)

Created via the API on 2026-08-29. All three are **draft** — the API cannot
publish a flow, so each has to be switched to Live in the UI.

| Flow | Flow id | Trigger metric | Template |
|---|---|---|---|
| Price Drop Alert | `VZFycG` | `Price Drop Found` (`RKceqK`) | `WakNR8` |
| Deadline Guard | `RKMJqA` | `Deadline Approaching` (`XnX9Yd`) | `XhJDAt` |
| Welcome — Forward your first booking | `UQYHkE` | `Account Created` (`UdnqFF`) | `TFzSsN` |

Klaviyo clones the template into the flow message on create, so editing the
template above does **not** change a flow already built from it — edit the
message inside the flow, or rebuild it from `docs/klaviyo-templates/`.

### Before any of this sends

1. **Flip each flow Draft → Live** (UI only).
2. **Mark each email transactional** — see §2; the API silently refuses.
3. **Verify the sending domain** for `hello@myroomwatch.com`. The account's
   default sender email is currently blank, so nothing sends until it's set.

Metric ids come from `GET /api/metrics`. A metric only exists once an event of
that name has been received, so bootstrap it (step 1, or a `backfill: true`
sample event) before building a flow that triggers on it.
