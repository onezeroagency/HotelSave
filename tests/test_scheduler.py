"""Tests for the deadline-clocked scheduler (§8)."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import models
from app.enums import JobStatus
from app.scheduler import worker
from app.security import hash_password


def _seed_job(session, **overrides):
    user = models.User(email="s@x.com", hashed_password=hash_password("password123"))
    session.add(user)
    session.flush()
    now = datetime.now(timezone.utc)
    defaults = dict(
        user_id=user.id,
        status=JobStatus.active.value,
        hotel_name_raw="Hotel Test",
        hotel_id="mock:hotel-test",
        check_in=date.today() + timedelta(days=10),
        check_out=date.today() + timedelta(days=13),
        nights=3,
        board_type="BB",
        adults=2,
        children=0,
        original_price=Decimal("400.00"),  # mock best (~120-180) is well under this
        currency="EUR",
        refundable=True,
        cancellation_deadline=now + timedelta(days=7),
        next_check_at=now - timedelta(minutes=1),  # due
    )
    defaults.update(overrides)
    job = models.MonitoringJob(**defaults)
    session.add(job)
    session.commit()
    return job


def test_cadence_table():
    assert worker.cadence_hours(30 * 24) == 24.0  # > 14 days
    assert worker.cadence_hours(7 * 24) == 12.0  # 3–14 days
    assert worker.cadence_hours(48) == 6.0  # final 72h


def test_run_once_detects_drop_and_records_history(db_session):
    session, _ = db_session
    job = _seed_job(session)

    processed = worker.run_once(db=session)
    assert processed == 1

    session.refresh(job)
    assert job.status == JobStatus.drop_found.value
    assert job.drop_alert_sent_at is not None
    assert job.current_best_price is not None
    assert job.lowest_seen_price == job.current_best_price
    assert job.check_count == 1
    assert job.next_check_at is not None  # rescheduled

    history = session.query(models.PriceCheck).filter_by(job_id=job.id).all()
    assert len(history) == 1
    assert history[0].best_matching_price == job.current_best_price


def test_expired_when_deadline_passed(db_session, monkeypatch):
    session, _ = db_session
    now = datetime.now(timezone.utc)
    job = _seed_job(session, cancellation_deadline=now - timedelta(hours=1))

    events: list[str] = []
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event", lambda name, email, props: events.append(name)
    )
    worker.run_once(db=session)
    session.refresh(job)

    assert job.status == JobStatus.expired.value
    assert job.next_check_at is None
    # Past the deadline we never price or fire an actionable alert (§8):
    assert job.check_count == 0  # no aggregator call
    assert events == ["Monitoring Ended"]  # not "Price Drop Found"


def test_paused_plan_is_skipped(db_session):
    session, _ = db_session
    job = _seed_job(session)
    job.user.plan_status = "past_due"
    session.commit()

    processed = worker.run_once(db=session)
    assert processed == 0
    session.refresh(job)
    assert job.check_count == 0
