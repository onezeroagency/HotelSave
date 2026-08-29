"""Polling scheduler (§8). Clock everything against the cancellation deadline,
not check-in — a drop found after the deadline is useless.

This is the deadline-clocked loop from the spec, wired to the PriceSource
interface and the §7 matching rules. Run it periodically (cron / worker every
~15 min); jobs self-schedule via next_check_at.

    python -m app.scheduler.worker         # one pass over due jobs
    python -m app.scheduler.worker --loop  # keep running, sleeping between passes
"""

import logging
import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..enums import JobStatus
from ..models import MonitoringJob, PriceCheck
from ..services import klaviyo, rebook
from ..services.matching import best_like_for_like, drop_floor, is_actionable_drop
from ..services.price_source import PriceSource, get_price_source

logger = logging.getLogger("hotelsave.scheduler")

LOOP_INTERVAL_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes (SQLite round-trips lose tz) as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hours_until(deadline: datetime | None, now: datetime) -> float | None:
    if deadline is None:
        return None
    return (_as_utc(deadline) - now).total_seconds() / 3600.0


def cadence_hours(hours_to_deadline: float | None) -> float:
    """Check frequency by time-to-deadline (§8 table)."""
    if hours_to_deadline is None:
        return 24.0  # no deadline known yet — default to daily
    days = hours_to_deadline / 24.0
    if days > 14:
        return 24.0  # 1×/day
    if days > 3:
        return 12.0  # 2×/day
    return 6.0  # final 72h — the money window, 4×/day


def _jitter_seconds() -> int:
    """Spread load so jobs don't all fire in the same cron minute (§8)."""
    return random.randint(0, 30 * 60)


def _retry_resolution(job: MonitoringJob, source: PriceSource) -> None:
    """§6b retry: same rule as create/ingestion — only a single high-confidence
    match may auto-resolve; ambiguity stays with the ask-the-user flow."""
    try:
        matches = source.resolve_hotel(
            job.hotel_name_raw, job.city, job.lat, job.lng, job.country
        )
    except Exception:
        logger.exception(
            "Hotel resolution retry failed for job %s (%r)", job.id, job.hotel_name_raw
        )
        return
    if len(matches) == 1 and matches[0].confidence >= 0.9:
        job.hotel_id = matches[0].hotel_id
        logger.info(
            "Resolved job %s to hotel_id=%s on scheduler retry", job.id, job.hotel_id
        )
    elif matches:
        logger.info(
            "Job %s resolution still ambiguous (%d candidates) — leaving for §6b flow",
            job.id,
            len(matches),
        )


def process_job(job: MonitoringJob, source: PriceSource, db: Session, now: datetime) -> None:
    hrs = hours_until(job.cancellation_deadline, now)

    # Expiry short-circuit (§8): once the free-cancellation window has closed or
    # the stay is over, stop — never price or fire an actionable alert on a
    # booking the user can no longer act on. Checked before any aggregator call.
    if (hrs is not None and hrs <= 0) or job.check_out <= now.date():
        delivered = klaviyo.emit_event(
            klaviyo.EVENT_MONITORING_ENDED,
            job.user.email,
            {
                "hotel": job.hotel_name_raw,
                "best_savings_seen": float(job.original_price - job.lowest_seen_price)
                if job.lowest_seen_price is not None
                else 0.0,
                "outcome": "drop_found" if job.drop_alert_sent_at else "no_drop",
            },
        )
        if delivered:
            job.status = JobStatus.expired.value
            job.next_check_at = None
        else:
            # Keep the job alive so the wrap-up is retried; expiring it here
            # would clear next_check_at and strand the undelivered event.
            logger.error(
                "Monitoring-ended event for job %s was NOT delivered — retrying "
                "next pass",
                job.id,
            )
            job.next_check_at = now + timedelta(hours=1)
        return

    # No hotel_id yet (§6b) — create-time resolution can fail transiently (bad
    # credentials, network, aggregator hiccup), so retry here instead of letting
    # the job idle forever. Found live 2026-08-29: a job sat unresolved and every
    # pass silently rescheduled it with no log.
    if not job.hotel_id:
        _retry_resolution(job, source)
    if not job.hotel_id:
        logger.warning(
            "Job %s (%r) still has no hotel_id — cannot price it; rescheduling",
            job.id,
            job.hotel_name_raw,
        )
        job.next_check_at = now + timedelta(hours=cadence_hours(hrs))
        return

    candidates = source.check(
        job.hotel_id, job.check_in, job.check_out, job.adults, job.children
    )
    best = best_like_for_like(candidates, job)

    db.add(
        PriceCheck(
            job_id=job.id,
            checked_at=now,
            best_matching_price=best.total_price if best else None,
            best_ota=best.ota if best else None,
        )
    )
    job.last_checked_at = now
    job.check_count += 1

    if best:
        job.current_best_price = best.total_price
        job.current_best_ota = best.ota
        job.current_best_url = best.deep_link
        # lowest_seen_price updates every check regardless of alerting (§8).
        if job.lowest_seen_price is None or best.total_price < job.lowest_seen_price:
            job.lowest_seen_price = best.total_price

    # --- drop detection (§8) ---
    # A drop the user cannot reach is not a saving. Until the price source
    # supplies rebook links, detection keeps running and lowest_seen_price keeps
    # updating (so the dashboard and the deadline email stay honest) — we just
    # don't email a number nobody can collect. See settings for the live case
    # that forced this.
    found_drop = is_actionable_drop(best, job)
    held = found_drop and settings.require_rebook_url_for_alerts and not best.deep_link
    if held:
        logger.warning(
            "Job %s: holding a %s %s drop from %s — no rebook link, so the user "
            "could not act on it (require_rebook_url_for_alerts).",
            job.id,
            job.currency,
            job.original_price - best.total_price,
            best.ota,
        )

    if found_drop and not held:
        deeper = (
            job.lowest_alerted_price is not None
            and best.total_price < job.lowest_alerted_price - drop_floor(job.original_price)
        )
        if job.drop_alert_sent_at is None or deeper:
            savings = job.original_price - best.total_price
            delivered = klaviyo.emit_event(
                klaviyo.EVENT_PRICE_DROP_FOUND,
                job.user.email,
                {
                    "hotel": job.hotel_name_raw,
                    # Which stay: a user watching two bookings must be able to
                    # tell them apart from the alert alone.
                    "city": job.city,
                    "check_in": job.check_in.isoformat(),
                    "check_out": job.check_out.isoformat(),
                    "nights": job.nights,
                    "board_type": job.board_type,
                    "adults": job.adults,
                    # What we actually matched, so the user can sanity-check the
                    # claim before acting — and so a bad match is visible in the
                    # email instead of only in the logs.
                    "booked_room": job.room_type_raw,
                    "found_room": best.room_name,
                    "found_on": best.ota,
                    "old_price": float(job.original_price),
                    "new_price": float(best.total_price),
                    "savings_amount": float(savings),
                    "savings_pct": round(float(savings / job.original_price * 100), 1),
                    "rebook_url": best.deep_link,
                    # Templates must branch on this: until an affiliate program
                    # supplies deep-links, rebook_url is null and a CTA button
                    # bound to it would render a dead link.
                    "has_rebook_url": bool(best.deep_link),
                    "currency": job.currency,
                    "cancellation_deadline": job.cancellation_deadline.isoformat()
                    if job.cancellation_deadline
                    else None,
                },
            )
            # Only record "the user has been told" if the alert actually went
            # out. Marking it on a failed send loses the saving silently and
            # never retries it — the one outcome this product must not produce.
            if delivered:
                job.status = JobStatus.drop_found.value
                job.drop_alert_sent_at = now
                job.lowest_alerted_price = best.total_price
            else:
                logger.error(
                    "Drop alert for job %s was NOT delivered — leaving it unsent "
                    "so the next pass retries",
                    job.id,
                )

    # --- deadline guard (§8): only if no actionable drop currently stands ---
    # A *held* drop must not suppress this: if we're staying quiet about a rate
    # the user can't reach, the deadline email is the only thing they get, and
    # it's the honest one — "we watched, nothing you can act on came up."
    if (
        hrs is not None
        and hrs <= settings.deadline_alert_hours
        and job.deadline_alert_sent_at is None
        and not (found_drop and not held)
    ):
        # Mode A (§9): rates moved somewhere, but on a feed we can't rebook from
        # — so movement changes the WORDING, never the trigger. The trigger is
        # the deadline, which we know from the user's own confirmation. Alerting
        # on cross-market movement itself would repeat the mistake that produced
        # the phantom EUR 94.91: a signal from a market the user isn't in.
        saw_movement = job.lowest_seen_price is not None and job.lowest_seen_price < (
            job.original_price - drop_floor(job.original_price)
        )
        check_url = rebook.check_prices_url(job)
        delivered = klaviyo.emit_event(
            klaviyo.EVENT_DEADLINE_APPROACHING,
            job.user.email,
            {
                "hotel": job.hotel_name_raw,
                "city": job.city,
                "check_in": job.check_in.isoformat(),
                "check_out": job.check_out.isoformat(),
                "cancellation_deadline": job.cancellation_deadline.isoformat()
                if job.cancellation_deadline
                else None,
                "checks_done": job.check_count,
                # The reassurance number: "we watched, it just never dropped."
                "lowest_seen_price": float(job.lowest_seen_price)
                if job.lowest_seen_price is not None
                else None,
                "currency": job.currency,
                # Mode A: "we've seen movement, worth a look" vs "nothing came
                # up". Deliberately no price figure attached to the movement
                # case — there is no tag a template could promise with.
                "saw_movement": saw_movement,
                "check_url": check_url,
                "has_check_url": bool(check_url),
            },
        )
        if delivered:
            job.status = JobStatus.deadline_soon.value
            job.deadline_alert_sent_at = now
        else:
            logger.error(
                "Deadline alert for job %s was NOT delivered — leaving it unsent "
                "so the next pass retries",
                job.id,
            )

    # --- schedule next check (expiry was handled up front) ---
    job.next_check_at = now + timedelta(hours=cadence_hours(hrs)) + timedelta(
        seconds=_jitter_seconds()
    )


def run_once(db: Session | None = None) -> int:
    """One pass: process every due, monitored job whose user can still monitor.
    Returns the number of jobs processed."""
    owns_session = db is None
    db = db or SessionLocal()
    source = get_price_source()
    now = _now()
    processed = 0
    try:
        stmt = select(MonitoringJob).where(
            MonitoringJob.status.in_([s.value for s in JobStatus.monitored()]),
            MonitoringJob.next_check_at.is_not(None),
            MonitoringJob.next_check_at <= now,
        )
        for job in db.scalars(stmt).all():
            if not job.user.can_monitor:  # paused for past_due/canceled (§11)
                continue
            try:
                process_job(job, source, db, now)
                processed += 1
            except Exception:
                logger.exception("Failed to process job %s", job.id)
        db.commit()
    finally:
        if owns_session:
            db.close()
    logger.info("Scheduler pass complete: %d job(s) processed", processed)
    return processed


def run_forever(interval: int = LOOP_INTERVAL_SECONDS) -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(level=logging.INFO)
    if "--loop" in sys.argv:
        run_forever()
    else:
        run_once()
