# Deployment runbook — api.myroomwatch.com + save@myroomwatch.com

Turns the repo into the live service: FastAPI backend + always-on scheduler +
Postgres on Render, Postmark receiving `save@` forwards, Klaviyo delivering
alerts. The static landing page stays on Vercel and is untouched by this.

```
forward booking → save@myroomwatch.com
      │  (email routing on the domain)
      ▼
Postmark Inbound ── webhook POST ──▶ api.myroomwatch.com/inbound/postmark?token=…
                                          │  parse (Claude) → resolve → MonitoringJob
                                          ▼
                        myroomwatch-scheduler (worker, §8 loop, LiteAPI)
                                          │  drop / deadline events
                                          ▼
                              Klaviyo flows → alert email to the user
```

## A. Backend on Render (~15 min)

1. render.com → **New → Blueprint** → connect GitHub → pick `onezeroagency/HotelSave`,
   branch = the default branch. Render reads `render.yaml` and proposes:
   `myroomwatch-api` (web), `myroomwatch-scheduler` (worker), `hotelsave-db` (Postgres).
2. It prompts for the `sync: false` secrets:
   | Var | Value |
   |---|---|
   | `LITEAPI_KEY` | sandbox key for now; **production key at launch** (both services) |
   | `ANTHROPIC_API_KEY` | console.anthropic.com key — powers `PARSER=claude` for real email parsing |
   | `KLAVIYO_API_KEY` | **private** key (`pk_…`) of the MyRoomWatch Klaviyo account; leave blank to keep dry-run logging |
   | `STRIPE_*` | leave blank until billing goes live |
   `SECRET_KEY` and `INBOUND_WEBHOOK_SECRET` are auto-generated; `DATABASE_URL` is injected.
3. Deploy. `alembic upgrade head` runs pre-deploy; the API is healthy when
   `https://myroomwatch-api.onrender.com/health` returns `{"status":"ok"}`.
4. **Custom domain:** service → Settings → Custom Domains → `api.myroomwatch.com`,
   then add the CNAME it shows at your DNS host. TLS is automatic.

Cost: ~$7 web + $7 worker + ~$6 Postgres ≈ **$20/mo**. (The web free tier spins
down on idle and would drop inbound webhooks — don't use it for the API.)

## B. Inbound email: save@myroomwatch.com (~20 min)

**The one-mail-system rule:** whoever holds the domain's MX records receives ALL
its mail. `hello@` (a human mailbox) and `save@` (machine parsing) must therefore
both live behind the **same** email-routing layer — do not point MX straight at
Postmark, or `hello@` dies.

1. **Postmark** (postmarkapp.com): create a server → **Inbound** stream. Copy
   - the server's **inbound address** (`<hash>@inbound.postmarkapp.com`), and
   - set its **webhook URL** to
     `https://api.myroomwatch.com/inbound/postmark?token=<INBOUND_WEBHOOK_SECRET>`
     (value: Render → myroomwatch-api → Environment).
2. **Email routing on the domain** (pick one):
   - **Cloudflare Email Routing** (free, if DNS is on Cloudflare): enable it (it
     sets the MX records), then rules: `hello@` → your personal inbox;
     `save@` → the Postmark inbound address. Cloudflare wants the destination
     verified — its confirmation email lands in **Postmark → Inbound message
     stream**, open it there and click the link.
   - **ImprovMX** (free tier, any DNS): add domain, set their MX, aliases
     `hello@` → personal inbox, `save@` → Postmark inbound address. No
     destination verification needed.
3. **Test:** email anything to `save@myroomwatch.com` → Postmark Inbound shows
   the message → your API logs show `POST /inbound/postmark` → reply behavior
   per §5/§6 (job created, or a pending_* prompt).

Postmark here is inbound-only: no sending DNS (SPF/DKIM) needed for it. The
Klaviyo sending-domain DNS (being added separately) covers outbound deliverability.

## C. Klaviyo alerts (~15 min, after DNS)

1. Put the MyRoomWatch account's **private** key into `KLAVIYO_API_KEY` on both
   Render services (until then, events are logged, not sent — safe default).
2. Bootstrap the metrics: `KLAVIYO_API_KEY=pk_… python scripts/bootstrap_klaviyo_metrics.py you@example.com`
3. Build Flow 1 (Price Drop) + Flow 2 (Deadline Guard) per `docs/klaviyo-setup.md`.

## D. End-to-end verification checklist

```bash
curl -s https://api.myroomwatch.com/health                          # {"status":"ok"}
# register + login + create a job against the real Amrita Hotel (see README);
# or forward a real confirmation email to save@myroomwatch.com
# then watch: Render → myroomwatch-scheduler → Logs for
#   "Resolved job … / Price Drop Found …" on the next §8 pass
```

- [ ] `/health` 200 on api.myroomwatch.com
- [ ] Email to `save@` appears in Postmark Inbound and hits the webhook (200)
- [ ] Job created from a forwarded email (`PARSER=claude` handles messy formats)
- [ ] Scheduler loop logs passes every 15 min; drop event emitted
- [ ] Klaviyo flow delivers the alert email (once C is done)
- [ ] Launch day: swap `LITEAPI_KEY` to the production key (both services) — no code change

## Security notes

- Secrets live only in Render env vars — never in the repo. `.env` is gitignored.
- The inbound webhook rejects calls without the `?token=` secret (401).
- LiteAPI production mode: complete its wizard with **WhiteLabel/Payment SDK**
  and a **virtual / low-limit card**; treat the production key like a password.
