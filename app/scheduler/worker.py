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
from ..services import klaviyo
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


def process_job(job: MonitoringJob, source: PriceSource, db: Session, now: datetime) -> None:
    hrs = hours_until(job.cancellation_deadline, now)

    # Expiry short-circuit (§8): once the free-cancellation window has closed or
    # the stay is over, stop — never price or fire an actionable alert on a
    # booking the user can no longer act on. Checked before any aggregator call.
    if (hrs is not None and hrs <= 0) or job.check_out <= now.date():
        job.status = JobStatus.expired.value
        job.next_check_at = None
        klaviyo.emit_event(
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
        return

    # Can't price a job we couldn't resolve to a hotel_id yet (§6b).
    if not job.hotel_id:
        job.next_check_at = now + timedelta(hours=cadence_hours(None))
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
    if is_actionable_drop(best, job):
        deeper = (
            job.lowest_alerted_price is not None
            and best.total_price < job.lowest_alerted_price - drop_floor(job.original_price)
        )
        if job.drop_alert_sent_at is None or deeper:
            savings = job.original_price - best.total_price
            klaviyo.emit_event(
                klaviyo.EVENT_PRICE_DROP_FOUND,
                job.user.email,
                {
                    "hotel": job.hotel_name_raw,
                    "old_price": float(job.original_price),
                    "new_price": float(best.total_price),
                    "savings_amount": float(savings),
                    "savings_pct": round(float(savings / job.original_price * 100), 1),
                    "rebook_url": best.deep_link,
                    "currency": job.currency,
                    "cancellation_deadline": job.cancellation_deadline.isoformat()
                    if job.cancellation_deadline
                    else None,
                },
            )
            job.status = JobStatus.drop_found.value
            job.drop_alert_sent_at = now
            job.lowest_alerted_price = best.total_price

    # --- deadline guard (§8): only if no actionable drop currently stands ---
    if (
        hrs is not None
        and hrs <= settings.deadline_alert_hours
        and job.deadline_alert_sent_at is None
        and not is_actionable_drop(best, job)
    ):
        klaviyo.emit_event(
            klaviyo.EVENT_DEADLINE_APPROACHING,
            job.user.email,
            {
                "hotel": job.hotel_name_raw,
                "cancellation_deadline": job.cancellation_deadline.isoformat()
                if job.cancellation_deadline
                else None,
                "checks_done": job.check_count,
            },
        )
        job.status = JobStatus.deadline_soon.value
        job.deadline_alert_sent_at = now

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
