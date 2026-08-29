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

## 2. The transactional gotcha — configure first

These are **transactional** alerts about a thing the user asked you to watch, so
they must send **regardless of marketing-consent status**. On every email action
in the flows below, set the message to **transactional** (Klaviyo: the email
action's settings → "This is a transactional message" / smart-sending off).
Otherwise a user who unsubscribes from marketing silently stops getting the price
drops they're paying for.

---

> **Ready-made templates:** paste-ready HTML for both flows lives in
> [`docs/klaviyo-templates/`](klaviyo-templates/) — `price-drop.html` and
> `deadline-guard.html`. They already brand-match the site, branch on
> `has_rebook_url`, and use only keys the backend actually emits. In Klaviyo:
> new email → drag in a block → source/HTML view → paste. **They cannot be
> created via the API from this repo's tooling** — the Klaviyo connector is
> pointed at a different account, so building them in the MyRoomWatch account's
> UI is deliberate, not a shortcut.

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

## 6. Programmatic creation (optional)

Flows can also be created via the API (`POST /api/flows`) with a `MetricTrigger`
referencing each metric's id + a transactional `SendEmailAction`. That path is
available but fiddlier than the UI; build in the UI first to nail the copy, then
export/automate if you want the flows version-controlled. Metric ids come from
`GET /api/metrics` after step 1.
