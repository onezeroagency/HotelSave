# Hotel Price-Drop Rebooking — MVP Build Spec

*Standalone subscription micro-SaaS. Monitors a user's refundable hotel booking, alerts on genuine price drops so they rebook cheaper — and guards the cancellation deadline so alerts always arrive in time to act.*

---

## 1. Positioning & the wedge

The product is **not** "check hotel prices." Every free tool does that. The product is:

> **We watch your specific booking, compare only like-for-like refundable rates, and guard your cancellation deadline — so a saving never slips past you.**

Free incumbents (Pruvo et al.) alert on drops but don't track when your free-cancellation window closes, so you can get an alert too late to use it. That deadline-guard is the paid wedge. It's also the *cheap* part to build (date math on data you already parsed), while the marketing spine leans on it.

Two revenue lines, aligned:

- **Subscription:** €7–15/month. This is the primary line.
- **Affiliate commission:** earned on the *rebook*, via the aggregator's deep-links. Scales with the value delivered, layered on top.

---

## 2. Architecture at a glance

```
Inbound email (forward-to)  ->  Parse (1 LLM call)  ->  Hotel-ID resolution
                                                              |
                                                              v
                                                      MonitoringJob (DB)
                                                              |
                                                   Deadline-clocked scheduler
                                                              |
                                                        Price check (aggregator API)
                                                              |
                                                   Like-for-like comparison
                                                        /            \
                                             drop found          deadline near, no drop
                                                   |                   |
                                          emit "Price Drop Found"   emit "Deadline Approaching"
                                                   \                  /
                                                    v                v
                                                   Klaviyo (events -> flows -> email)

Billing: Stripe subscription (separate, standard).
```

Principle: **backend detects, Klaviyo delivers.** The backend never sends email directly — it pushes events to Klaviyo, and flows handle all copy, timing, and lifecycle.

---

## 3. Data model

The entire system is one object with a state machine around it. Everything else is plumbing.

```
User
  id
  email
  stripe_customer_id
  plan_status              # trialing | active | past_due | canceled
  created_at

MonitoringJob
  id
  user_id
  status                   # see state machine (section 4)

  # --- hotel identity ---
  hotel_name_raw           # as parsed from the confirmation
  hotel_id                 # aggregator's internal hotel ID (resolved — section 6)
  city
  country
  lat, lng                 # disambiguate the hotel match

  # --- the stay ---
  check_in                 # date
  check_out                # date
  nights                   # derived

  # --- the room (this tuple defines "same room") ---
  room_type_raw            # "Superior Double, City View" as written
  board_type               # RO | BB | HB | FB
  adults
  children

  # --- the rate to beat ---
  original_price           # total for the stay
  currency
  original_ota             # Booking | Expedia | Direct | ...

  # --- policy: the actionability clock ---
  refundable               # bool — must be true to monitor
  cancellation_deadline    # datetime — the single most important field
  cancellation_terms_raw   # kept for audit / display

  # --- monitoring state ---
  created_at
  last_checked_at
  next_check_at
  check_count
  current_best_price
  current_best_ota
  current_best_url         # affiliate deep-link to rebook
  lowest_seen_price

  # --- alerting idempotency guards ---
  drop_alert_sent_at
  deadline_alert_sent_at

PriceCheck        # optional history table, powers trend emails & analytics
  id
  job_id
  checked_at
  best_matching_price      # null if no like-for-like match found this check
  best_ota
  raw_response_ref         # pointer to stored payload for debugging
```

Keep `PriceCheck` from day one even if you don't surface it — the history is what powers "we've been watching, here's the trend" retention content later, and it's invaluable for debugging bad matches.

---

## 4. State machine

```
                +-----------+
   job created  |  active   |
  ------------> |           |
                +-----------+
                  |   |   |
   drop found ----+   |   +---- 48h before deadline, no actionable drop
        |             |                     |
        v             |                     v
  +------------+      |              +---------------+
  | drop_found |      |              | deadline_soon |
  +------------+      |              +---------------+
        |             |                     |
        |   (still monitors until deadline; can re-alert on a deeper drop)
        |             |                     |
        +------+------+---------------------+
               |
        deadline passes OR checked out
               |
               v
        +-----------+        user cancels sub / deletes booking
        |  expired  | <---------------------------------------  cancelled
        +-----------+
```

- `active` → `drop_found`: a like-for-like refundable rate beats original by more than the floor. Fire `Price Drop Found`. **Stays monitoring** — a deeper drop later can re-alert (guarded by not re-firing on trivial improvements).
- `active`/`drop_found` → `deadline_soon`: 48h before `cancellation_deadline`. Fire `Deadline Approaching` **only if** no actionable drop currently stands.
- any → `expired`: `cancellation_deadline` passed, or `check_out` reached. Stop checking. Emit `Monitoring Ended`.
- any → `cancelled`: user cancels subscription or removes the booking.

---

## 5. Ingestion — forward-to email

Ship **email forwarding**, not Gmail OAuth. Users balk at "read access to all your mail," and a forward-to address sidesteps the entire trust problem.

- Stand up an inbound-email address, e.g. `save@[brand].com`, via an inbound-email provider (Postmark inbound, SendGrid inbound parse, or Mailgun routes).
- Match the sender to a `User` by from-address. Unknown sender → auto-reply inviting them to sign up (acquisition hook).
- Hand the raw email body to the parser (section 6).
- **v2 only:** Gmail integration, and only if users ask for it.

---

## 6. Parse + resolve

### 6a. Parse (one LLM call)

OTAs phrase room names inconsistently — this is a job for an LLM, not regex. Draft prompt:

```
You extract structured hotel booking data from a forwarded confirmation email.
Return ONLY valid JSON, no prose, no markdown fences. Use null for anything not
clearly stated. Do not guess.

Schema:
{
  "hotel_name": string,
  "city": string,
  "country": string | null,
  "address": string | null,
  "check_in": "YYYY-MM-DD",
  "check_out": "YYYY-MM-DD",
  "room_type_raw": string,
  "board_type": "RO" | "BB" | "HB" | "FB" | null,
  "adults": integer | null,
  "children": integer | null,
  "total_price": number,
  "currency": string,          // ISO 4217, e.g. "EUR"
  "ota": string,               // e.g. "Booking.com", "Expedia", "Direct"
  "refundable": boolean | null,
  "cancellation_deadline": "YYYY-MM-DDTHH:MM" | null
}

Email:
<<<
{{ raw_email_text }}
>>>
```

Post-parse guards:
- If `refundable` is not clearly true → don't start monitoring; email the user: "We can only watch refundable bookings — is this one refundable? Reply with the cancellation date." (Also your chance to teach them to book refundable next time.)
- If `cancellation_deadline` is null but `refundable` is true → ask for it once, store it.

### 6b. Hotel-ID resolution — the step everyone forgets

You have a hotel *name + city*. The API needs *its* hotel ID, and one name can match many properties. Between parse and monitor:

1. Query the aggregator's hotel lookup by name + city.
2. Disambiguate with `lat/lng` or `address`.
3. **High confidence** (single strong match): store `hotel_id`, proceed.
4. **Low confidence** (multiple / weak): email the user a "which of these is your hotel?" pick-list once, store the answer, never ask again.

A wrong `hotel_id` means every alert is garbage. This guard rail is cheap insurance.

---

## 7. Room matching — correctness *is* ethics

A candidate rate counts as a **drop** only if **all** hold:

1. Same `hotel_id`, same `check_in`/`check_out`.
2. Matches on `{adults, children, board_type}`.
3. Candidate is itself `refundable = true`.
4. Candidate total beats `original_price` by more than the floor: `max(€10, 3%)`.

Rule 3 is doing double duty: it's the correctness constraint (never compare a flexible rate against a non-refundable trap) and the ethical line (you never nudge someone into a worse product to manufacture a "saving"). The floor in rule 4 kills currency-noise and €2-wiggle spam that would erode trust.

---

## 8. Polling scheduler

Two principles do all the work:

- Hotel prices don't move minute-to-minute — a few checks/day is plenty.
- Clock everything against the **cancellation deadline**, not check-in. A drop found after the deadline is useless, because rebooking only helps while you can still cancel the original for free.

### Cadence (by time-to-deadline)

| Time until cancellation deadline | Check frequency |
|---|---|
| > 14 days | 1× / day |
| 3–14 days | 2× / day |
| final 72 hours | every 6h (4× / day) — the money window |
| deadline passed | stop → `expired` |

### Scheduler pseudocode

```
for each job where status in (active, drop_found, deadline_soon)
    and next_check_at <= now:

    result = aggregator.check(job.hotel_id, job.check_in, job.check_out,
                              job.adults, job.children)

    best = best_like_for_like(result, job)      # applies section 7 rules
    record PriceCheck(job, best)
    job.last_checked_at = now
    job.check_count += 1
    if best: update current_best_* and lowest_seen_price

    # --- drop detection ---
    if best and best.total < job.original_price - floor(job):
        if not job.drop_alert_sent_at
           or best.total < job.lowest_alerted_price - floor(job):   # deeper drop
            emit_klaviyo("Price Drop Found", payload(job, best))
            job.status = drop_found
            job.drop_alert_sent_at = now

    # --- deadline guard ---
    if hours_until(job.cancellation_deadline) <= 48
       and not job.deadline_alert_sent_at
       and not currently_actionable_drop(job):
        emit_klaviyo("Deadline Approaching", payload(job))
        job.status = deadline_soon
        job.deadline_alert_sent_at = now

    # --- schedule next ---
    job.next_check_at = now + cadence(hours_until(job.cancellation_deadline))
                              + random_jitter()   # spread load, avoid burst pattern

    if past_deadline(job) or checked_out(job):
        job.status = expired
        emit_klaviyo("Monitoring Ended", payload(job))
```

Add random jitter so jobs don't all fire in the same cron minute. Update `lowest_seen_price` on every check regardless of alerting — it feeds retention content and analytics.

---

## 9. Data source

Build against a **commission-based aggregator** (e.g. Travelpayouts / Hotellook) from day one. Reasoning:

- It aggregates across OTAs, so you see the same property across Booking/Expedia/Agoda in one call — needed because your user may have booked on any channel.
- It monetizes via commission, not per-call fees, so call volume grows with paying users while per-call cost stays ~zero.
- Deep-links are affiliate links → your rebook revenue line comes for free.

**Cost trap to avoid:** free tiers (e.g. Amadeus' ~2,000 calls/month) cover ~30 bookings at 2 checks/day — fine for a prototype, useless at scale. If you prototype on a free tier, keep the price-source behind an interface so swapping to the aggregator is a one-file change, not a re-plumb.

**Avoid** unofficial scraper APIs on RapidAPI — not backed by OTA partnerships, ToS risk, and they reintroduce exactly the fragility this project was chosen to avoid.

---

## 10. Klaviyo — events & flows

Architecture: **backend emits events → Klaviyo flows trigger off them.** All copy/timing/testing lives in Klaviyo.

### Events (backend → Klaviyo API)

| Event | When | Key properties |
|---|---|---|
| `Booking Monitored` | job created | hotel, city, check_in, check_out, original_price, currency, cancellation_deadline |
| `Price Drop Found` | actionable like-for-like drop | old_price, new_price, savings_amount, savings_pct, rebook_url, hotel, cancellation_deadline |
| `Deadline Approaching` | 48h pre-deadline, no drop | hotel, cancellation_deadline, checks_done |
| `Monitoring Ended` | deadline passed / checked out | hotel, best_savings_seen, outcome |

### Flow 1 — Price Drop Alert  *(trigger: `Price Drop Found`)*

Immediate send. Subject leads with the number. One job only: show the saving, the rebook button, and the safe sequence.

> **Subject:** Save €{{ savings_amount }} at {{ hotel }} — rebook now
>
> Good news — the price on your exact room at **{{ hotel }}** dropped.
>
> - You paid: €{{ old_price }}
> - New rate (same room, same dates, free cancellation): **€{{ new_price }}**
> - You save: **€{{ savings_amount }} ({{ savings_pct }}%)**
>
> **[ Rebook at the lower rate → ]**({{ rebook_url }})
>
> ⚠️ Do it in this order: **book the new rate first, confirm it, then cancel your original.** Never cancel first — you could lose the room.
>
> Your free-cancellation window closes **{{ cancellation_deadline }}**, so don't sit on it.

- Conditional split on `savings_amount` for urgency styling on big wins.
- One 24h follow-up **only if** not clicked and deadline is near.
- The rebook link is your affiliate deep-link — this flow both delights the user and books your commission.

### Flow 2 — Deadline Guard  *(trigger: `Deadline Approaching`)*

The flow the free tools don't have. Your entire retention story: the product visibly working even when there's no win.

> **Subject:** Still watching {{ hotel }} — your free-cancel window closes soon
>
> Quick heads-up: your free-cancellation window at **{{ hotel }}** closes in about **48 hours** ({{ cancellation_deadline }}).
>
> We've checked {{ checks_done }} times and haven't found a lower rate yet — and we'll keep watching right up until the window closes. If anything drops, you'll hear from us instantly.
>
> Either way, here are your booking details so nothing slips by. Safe travels.

On a service that produces a saving only ~40–50% of the time, this email is what stops the other half from asking "why am I paying." It converts silence into perceived protection — arguably more important to retention than the drop alert is to acquisition.

### Klaviyo gotcha

These are **transactional** messages (alerts about a thing the user asked you to watch), so they must send regardless of marketing-consent status. Configure that up front — otherwise a user who unsubscribes from your newsletter silently stops getting the price drops they're paying for.

---

## 11. Billing

- Stripe subscription, €7–15/month (settle the number after the economics check — section 13).
- Suggested funnel: **first booking monitored free**, show a real save, then gate further monitoring. "We already found you €{{ x }} — keep watching every booking for €9/mo" is a strong, honest conversion.
- Map Stripe subscription status → `User.plan_status`; pause monitoring for `past_due`/`canceled`.

---

## 12. Suggested stack

Nothing exotic — this is a small, boring, reliable system by design.

- **Inbound email:** Postmark inbound (clean parsing, good deliverability).
- **App + API:** whatever you ship fastest in — a single web service is enough.
- **DB:** Postgres (`User`, `MonitoringJob`, `PriceCheck`).
- **Scheduler:** a cron/worker running the section-8 loop every ~15 min; jobs self-schedule via `next_check_at`.
- **Price data:** aggregator behind a `PriceSource` interface.
- **Parse:** one LLM call per inbound booking.
- **Email/lifecycle:** Klaviyo (events API + flows).
- **Billing:** Stripe.

---

## 13. Build sequence

1. **Skeleton:** DB schema + `MonitoringJob` CRUD + Stripe subscription + auth.
2. **Ingestion:** inbound email → parse → resolve hotel_id → create job. Prove end-to-end with your own real bookings.
3. **Monitoring:** aggregator integration behind the interface + scheduler + like-for-like matching. Watch it detect a real drop on a real booking.
4. **Alerting:** wire the four Klaviyo events + build Flows 1 & 2.
5. **Launch narrow:** one traveler niche, Meta acquisition, first-booking-free funnel.

Everything past step 5 — Gmail integration, flights, a dashboard, multi-currency polish — is v2.

---

## 14. Decisions still open (pre-code)

- **Economics check:** aggregator commission + subscription vs realistic conversion and churn — does €9/month clear against Meta CAC before any code is written?
- **Price point:** €7 / €9 / €15 — depends on the above.
- **Niche to launch into first:** which traveler segment has the cleanest Meta targeting + highest refundable-booking rate?
- **Brand:** name, domain, positioning line (the "flights, then hotels — never overpay" energy, but standalone).
