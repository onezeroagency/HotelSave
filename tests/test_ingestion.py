"""Tests for the ingestion path (§5, §6): email → parse → resolve → job."""

import pytest

from app import models
from app.enums import JobStatus
from app.security import hash_password
from app.services import klaviyo
from app.services.email_inbound import InboundEmail
from app.services.ingestion import process_inbound_email


@pytest.fixture()
def captured_events(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event",
        lambda name, email, props: events.append((name, email, props)),
    )
    return events


def _make_user(session, email="traveler@example.com"):
    user = models.User(email=email, hashed_password=hash_password("password123"))
    session.add(user)
    session.commit()
    return user


def _email(from_email="traveler@example.com", **fields):
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return InboundEmail(from_email=from_email, subject="Booking", text_body=lines, html_body=None)


_MONITORABLE = dict(
    Hotel="Grand Vilnius",
    City="Vilnius",
    Check_in="2026-09-10",
    Check_out="2026-09-13",
    Board="BB",
    Adults="2",
    Total="420",
    Currency="EUR",
    Refundable="Yes",
    Cancellation="2026-09-07T23:59",
)


def _fields(**overrides):
    # dataclass helper keys use "Check-in" etc.; map underscores back.
    base = {k.replace("_", "-"): v for k, v in _MONITORABLE.items()}
    for k, v in overrides.items():
        base[k.replace("_", "-")] = v
    return base


def test_unknown_sender_invites_signup(db_session, captured_events):
    session, _ = db_session
    result = process_inbound_email(session, _email(from_email="stranger@example.com"))
    assert result.status == "unknown_sender"
    assert captured_events[0][0] == klaviyo.EVENT_SIGNUP_INVITE


def test_non_refundable_is_not_monitored(db_session, captured_events):
    session, _ = db_session
    _make_user(session)
    fields = _fields()
    fields["Refundable"] = "No"
    result = process_inbound_email(session, _email(**fields))
    assert result.status == "not_refundable"
    assert session.query(models.MonitoringJob).count() == 0
    assert captured_events[-1][0] == klaviyo.EVENT_NEEDS_REFUNDABLE


def test_full_booking_starts_monitoring(db_session, captured_events):
    session, _ = db_session
    _make_user(session)
    result = process_inbound_email(session, _email(**_fields()))
    assert result.status == "monitoring"

    job = session.get(models.MonitoringJob, result.job_id)
    assert job.status == JobStatus.active.value
    assert job.hotel_id == "mock:grand-vilnius"  # resolved (city present)
    assert job.next_check_at is not None
    assert job.nights == 3
    assert klaviyo.EVENT_BOOKING_MONITORED in [e[0] for e in captured_events]


def test_ambiguous_hotel_goes_pending(db_session, captured_events):
    session, _ = db_session
    _make_user(session)
    fields = _fields()
    del fields["City"]  # no city → mock returns a weak multi-match
    result = process_inbound_email(session, _email(**fields))
    assert result.status == "pending_hotel"

    job = session.get(models.MonitoringJob, result.job_id)
    assert job.status == JobStatus.pending_hotel.value
    assert job.hotel_id is None
    assert job.hotel_candidates is not None  # pick-list stashed
    assert klaviyo.EVENT_HOTEL_RESOLUTION_NEEDED in [e[0] for e in captured_events]


def test_missing_deadline_goes_pending(db_session, captured_events):
    session, _ = db_session
    _make_user(session)
    fields = _fields()
    del fields["Cancellation"]  # refundable but no deadline
    result = process_inbound_email(session, _email(**fields))
    assert result.status == "pending_info"

    job = session.get(models.MonitoringJob, result.job_id)
    assert job.status == JobStatus.pending_info.value
    assert job.hotel_id is not None  # hotel resolved, only the deadline is missing
    assert klaviyo.EVENT_NEEDS_DEADLINE in [e[0] for e in captured_events]


def test_webhook_then_patch_activates(client):
    """End-to-end: pending_info job created via the Postmark webhook, then the
    user supplies the deadline via PATCH and it flips to active (§6)."""
    client.post(
        "/auth/register", json={"email": "traveler@example.com", "password": "password123"}
    )
    token = client.post(
        "/auth/login", data={"username": "traveler@example.com", "password": "password123"}
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    body = "\n".join(
        f"{k}: {v}" for k, v in _fields().items() if k != "Cancellation"
    )
    resp = client.post(
        "/inbound/postmark",
        json={"FromFull": {"Email": "traveler@example.com"}, "TextBody": body},
    )
    assert resp.json()["status"] == "pending_info"
    job_id = resp.json()["job_id"]

    patched = client.patch(
        f"/jobs/{job_id}",
        json={"cancellation_deadline": "2026-09-07T23:59:00"},
        headers=auth,
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "active"
