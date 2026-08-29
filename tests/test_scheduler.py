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
        # Must report delivery success — the worker only closes a job once the
        # user has actually been told (see test_expiry_retries_when_undelivered).
        "app.services.klaviyo.emit_event",
        lambda name, email, props: (events.append(name), True)[1],
    )
    worker.run_once(db=session)
    session.refresh(job)

    assert job.status == JobStatus.expired.value
    assert job.next_check_at is None
    # Past the deadline we never price or fire an actionable alert (§8):
    assert job.check_count == 0  # no aggregator call
    assert events == ["Monitoring Ended"]  # not "Price Drop Found"


def test_expiry_retries_when_undelivered(db_session, monkeypatch):
    """Expiring on a failed send would clear next_check_at and strand the wrap-up."""
    session, _ = db_session
    now = datetime.now(timezone.utc)
    job = _seed_job(session, cancellation_deadline=now - timedelta(hours=1))

    monkeypatch.setattr("app.services.klaviyo.emit_event", lambda *a, **k: False)
    worker.run_once(db=session)
    session.refresh(job)

    assert job.status == JobStatus.active.value  # still open, not silently closed
    assert job.next_check_at is not None  # and it will come back around

    # The retry is deliberately an hour out, so wind the clock forward to it.
    job.next_check_at = now - timedelta(minutes=1)
    session.commit()

    # Once Klaviyo recovers, that pass closes it out.
    monkeypatch.setattr("app.services.klaviyo.emit_event", lambda *a, **k: True)
    worker.run_once(db=session)
    session.refresh(job)

    assert job.status == JobStatus.expired.value
    assert job.next_check_at is None


def test_paused_plan_is_skipped(db_session):
    session, _ = db_session
    job = _seed_job(session)
    job.user.plan_status = "past_due"
    session.commit()

    processed = worker.run_once(db=session)
    assert processed == 0
    session.refresh(job)
    assert job.check_count == 0


def test_unresolved_job_is_resolved_on_scheduler_retry(db_session):
    """§6b retry (found live 2026-08-29): create-time resolution can fail
    transiently; the worker must retry instead of idling the job forever."""
    session, _ = db_session
    job = _seed_job(session, hotel_id=None, city="Vilnius")

    worker.run_once(session)
    session.refresh(job)

    # Mock source resolves name+city to a single 0.95 match → job self-heals
    # on this very pass and gets priced (mock best is well under 400).
    assert job.hotel_id is not None
    assert job.check_count == 1
    assert job.current_best_price is not None


def test_unresolvedable_job_is_rescheduled_not_priced(db_session):
    """Ambiguous resolution (no city → multi-match in the mock) must NOT
    auto-resolve; the job is rescheduled and left for the ask-the-user flow."""
    session, _ = db_session
    job = _seed_job(session, hotel_id=None, city=None)

    worker.run_once(session)
    session.refresh(job)

    assert job.hotel_id is None
    assert job.check_count == 0
    assert job.next_check_at is not None


def test_drop_event_identifies_the_stay_and_flags_missing_rebook_url(db_session, monkeypatch):
    """The alert must name which stay dropped (a user can watch several) and tell
    the template whether a rebook link exists — the mock source supplies one, so
    has_rebook_url is True here; it is False until an affiliate program lands."""
    session, _ = db_session
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        worker.klaviyo, "emit_event", lambda name, email, props: events.append((name, email, props))
    )
    job = _seed_job(session, city="Vilnius")

    worker.run_once(session)

    drop = next(p for name, _, p in events if name == worker.klaviyo.EVENT_PRICE_DROP_FOUND)
    assert drop["city"] == "Vilnius"
    assert drop["check_in"] == job.check_in.isoformat()
    assert drop["check_out"] == job.check_out.isoformat()
    assert drop["adults"] == job.adults
    assert drop["has_rebook_url"] is bool(drop["rebook_url"])
    assert drop["savings_amount"] > 0


def test_failed_alert_is_retried_then_sent_once(db_session, monkeypatch):
    """A drop alert that Klaviyo rejects must NOT be recorded as sent — otherwise
    the saving is lost silently and never retried (seen live 2026-08-29: a 401
    from Klaviyo still marked the job alerted). Once delivered, it must not
    repeat."""
    session, _ = db_session
    job = _seed_job(session)

    # Delivery fails (e.g. bad API key) → nothing recorded, job stays alertable.
    monkeypatch.setattr(worker.klaviyo, "emit_event", lambda *a, **k: False)
    worker.run_once(session)
    session.refresh(job)
    assert job.drop_alert_sent_at is None
    assert job.status == JobStatus.active.value

    # Delivery succeeds on a later pass → recorded exactly once.
    sent: list[str] = []
    monkeypatch.setattr(
        worker.klaviyo, "emit_event", lambda name, *a, **k: (sent.append(name), True)[1]
    )
    job.next_check_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()
    worker.run_once(session)
    session.refresh(job)
    assert job.drop_alert_sent_at is not None
    assert job.status == JobStatus.drop_found.value
    assert sent.count(worker.klaviyo.EVENT_PRICE_DROP_FOUND) == 1

    # And never again for the same drop.
    sent.clear()
    job.next_check_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()
    worker.run_once(session)
    assert worker.klaviyo.EVENT_PRICE_DROP_FOUND not in sent


class _LinklessSource:
    """A price source that finds a real drop but can't say where to rebook it —
    the shape of the LiteAPI feed before an affiliate program supplies links."""

    def __init__(self, room_name="Standard Double Room"):
        self.room_name = room_name

    def resolve_hotel(self, *a, **k):
        return []

    def check(self, *a, **k):
        from app.services.price_source.base import RateCandidate

        return [
            RateCandidate(
                ota="LiteAPI",
                total_price=Decimal("305.09"),  # well under the 400 original
                currency="EUR",
                board_type="BB",
                adults=None,
                children=None,
                refundable=True,
                deep_link=None,
                room_name=self.room_name,
            )
        ]


def test_unreachable_drop_is_held_not_emailed(db_session, monkeypatch):
    """Live 2026-08-29: a booking made 20 min earlier at the cheapest refundable
    rate its OTA sold was reported as a EUR 94.91 drop, from a supplier the user
    had no link to. Detecting it is fine; emailing it is not."""
    session, _ = db_session
    job = _seed_job(session)

    events = []
    monkeypatch.setattr(worker, "get_price_source", lambda: _LinklessSource())
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event",
        lambda name, email, props: (events.append(name), True)[1],
    )
    worker.run_once(db=session)
    session.refresh(job)

    assert worker.klaviyo.EVENT_PRICE_DROP_FOUND not in events
    assert job.status == JobStatus.active.value
    assert job.drop_alert_sent_at is None
    # Detection still ran: the dashboard must show what we saw, or the product
    # looks asleep rather than honest.
    assert job.lowest_seen_price == Decimal("305.09")
    assert job.check_count == 1


def test_held_drop_still_lets_the_deadline_email_through(db_session, monkeypatch):
    """Holding the drop must not silence the deadline guard too — otherwise the
    user gets nothing at all."""
    session, _ = db_session
    now = datetime.now(timezone.utc)
    _seed_job(session, cancellation_deadline=now + timedelta(hours=12))

    events = []
    monkeypatch.setattr(worker, "get_price_source", lambda: _LinklessSource())
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event",
        lambda name, email, props: (events.append(name), True)[1],
    )
    worker.run_once(db=session)

    assert worker.klaviyo.EVENT_PRICE_DROP_FOUND not in events
    assert worker.klaviyo.EVENT_DEADLINE_APPROACHING in events


def test_drop_is_emailed_once_links_exist(db_session, monkeypatch):
    """The gate is temporary — with the affiliate layer live it must not block."""
    session, _ = db_session
    job = _seed_job(session)

    monkeypatch.setattr(worker.settings, "require_rebook_url_for_alerts", False)
    events = []
    monkeypatch.setattr(worker, "get_price_source", lambda: _LinklessSource())
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event",
        lambda name, email, props: (events.append(name), True)[1],
    )
    worker.run_once(db=session)
    session.refresh(job)

    assert worker.klaviyo.EVENT_PRICE_DROP_FOUND in events
    assert job.status == JobStatus.drop_found.value
