"""Klaviyo account wiring (VAJcs5): signup list + consent gating, parse-failed
event, watch profile properties, and the richer Monitoring Ended payload."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import models
from app.enums import JobStatus
from app.services import klaviyo, mailer
from app.services.email_inbound import InboundEmail
from app.services.ingestion import process_inbound_email
from app.services.ratelimit import magic_link_limiter


@pytest.fixture(autouse=True)
def _fresh_limiter():
    magic_link_limiter.reset()
    yield
    magic_link_limiter.reset()


@pytest.fixture()
def klaviyo_calls(monkeypatch):
    """Capture every outbound Klaviyo API call at the _post seam."""
    calls = []

    def fake_post(url, payload, what):
        calls.append({"url": url, "payload": payload, "what": what})
        if "profile-import" in url:
            return {"data": {"id": "PROFILE1"}}
        return {}

    monkeypatch.setattr(klaviyo, "_post", fake_post)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: True)
    return calls


def _urls(calls):
    return [c["url"] for c in calls]


# --- signup wiring -----------------------------------------------------------

def test_magic_signup_adds_to_signups_list_never_email_list(client, klaviyo_calls):
    client.post("/auth/magic-link", json={"email": "n@example.com"})
    urls = " ".join(_urls(klaviyo_calls))
    assert f"/lists/{klaviyo.LIST_SIGNUPS}/relationships/profiles/" in urls
    assert klaviyo.LIST_SIGNUPS == "RZ4A3b"
    assert "RHXEeq" not in urls  # the generic "Email List" must never be used


def test_no_opt_in_means_no_marketing_subscription(client, klaviyo_calls):
    client.post("/auth/magic-link", json={"email": "n@example.com"})
    assert not any("subscription" in u for u in _urls(klaviyo_calls))


def test_opt_in_subscribes_and_lands_on_profile(client, klaviyo_calls):
    client.post("/auth/magic-link", json={"email": "n@example.com", "marketing_opt_in": True})
    subs = [c for c in klaviyo_calls if "profile-subscription-bulk-create-jobs" in c["url"]]
    assert len(subs) == 1
    attrs = subs[0]["payload"]["data"]
    assert attrs["relationships"]["list"]["data"]["id"] == klaviyo.LIST_SIGNUPS
    profile = attrs["attributes"]["profiles"]["data"][0]["attributes"]
    assert profile["subscriptions"]["email"]["marketing"]["consent"] == "SUBSCRIBED"

    imports = [c for c in klaviyo_calls if "profile-import" in c["url"]]
    props = imports[0]["payload"]["data"]["attributes"]["properties"]
    assert props["marketing_opt_in"] is True and props["marketing_opt_in_at"]


def test_later_opt_in_upgrade_subscribes_existing_user(client, klaviyo_calls):
    client.post("/auth/magic-link", json={"email": "n@example.com"})
    before = len([c for c in klaviyo_calls if "subscription" in c["url"]])
    client.post("/auth/magic-link", json={"email": "n@example.com", "marketing_opt_in": True})
    after = len([c for c in klaviyo_calls if "subscription" in c["url"]])
    assert before == 0 and after == 1


def test_account_created_event_carries_consent(client, klaviyo_calls, monkeypatch):
    events = []
    monkeypatch.setattr(
        klaviyo, "emit_event", lambda n, e, p: (events.append((n, e, p)), True)[1]
    )
    client.post("/auth/magic-link", json={"email": "n@example.com", "marketing_opt_in": True})
    created = [p for n, _, p in events if n == klaviyo.EVENT_ACCOUNT_CREATED]
    assert created and created[0]["marketing_opt_in"] is True
    assert created[0]["marketing_opt_in_at"]


# --- parse failed ------------------------------------------------------------

def test_unparseable_forward_emits_parse_failed(client, db_session, monkeypatch):
    session, _ = db_session
    user = models.User(email="t@example.com", hashed_password=None)
    session.add(user)
    session.commit()

    events = []
    monkeypatch.setattr(
        "app.services.klaviyo.emit_event",
        lambda n, e, p: (events.append((n, e, p)), True)[1],
    )

    class NoParser:
        def parse(self, body):
            return None

    monkeypatch.setattr("app.services.ingestion.get_parser", lambda: NoParser())
    result = process_inbound_email(
        session,
        InboundEmail(from_email="t@example.com", subject="Fwd: hi", text_body="??", html_body=None),
    )
    assert result.status == "parse_failed"
    assert events == [(klaviyo.EVENT_PARSE_FAILED, "t@example.com", {"subject": "Fwd: hi"})]


# --- watch profile properties ------------------------------------------------

def _job(id, status, check_in, check_out, hotel="H"):
    j = models.MonitoringJob(
        user_id=1, hotel_name_raw=hotel, check_in=check_in, check_out=check_out,
        nights=1, original_price=Decimal("100"), currency="EUR", refundable=True,
        status=status,
    )
    j.id = id
    return j


def test_watch_profile_properties_shapes():
    a = _job(1, JobStatus.active.value, date(2027, 1, 10), date(2027, 1, 12), "Alpha")
    b = _job(2, JobStatus.expired.value, date(2026, 5, 1), date(2026, 5, 3), "Beta")
    c = _job(3, JobStatus.deadline_soon.value, date(2027, 2, 1), date(2027, 2, 4), "Gamma")
    p = klaviyo.watch_profile_properties([a, b, c])
    assert p == {
        "watch_status": "watching",
        "watch_count": 3,
        "active_watch_count": 2,
        "last_hotel": "Gamma",
        "next_checkin_date": "2027-01-10",
        "last_checkout_date": "2027-02-04",
    }
    assert klaviyo.watch_profile_properties([b])["watch_status"] == "ended"
    assert klaviyo.watch_profile_properties([])["watch_status"] == "none"
    assert klaviyo.watch_profile_properties([])["last_hotel"] is None


def test_monitored_booking_syncs_profile(client, db_session, monkeypatch, klaviyo_calls):
    """End to end through ingestion: a monitorable forward upserts the watch
    profile with the new properties."""
    session, _ = db_session
    user = models.User(email="t@example.com", hashed_password=None)
    session.add(user)
    session.commit()
    monkeypatch.setattr(klaviyo, "emit_event", lambda *a: True)

    fields = {
        "Hotel": "Grand Vilnius", "City": "Vilnius", "Check-in": "2027-09-10",
        "Check-out": "2027-09-13", "Board": "BB", "Adults": "2", "Total": "420",
        "Currency": "EUR", "Refundable": "Yes", "Cancellation": "2027-09-07T23:59",
    }
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    result = process_inbound_email(
        session, InboundEmail(from_email="t@example.com", subject="B", text_body=body, html_body=None)
    )
    assert result.status == "monitoring"
    imports = [c for c in klaviyo_calls if "profile-import" in c["url"]]
    assert imports, "no profile sync happened"
    props = imports[-1]["payload"]["data"]["attributes"]["properties"]
    assert props["watch_status"] == "watching"
    assert props["active_watch_count"] == 1
    assert props["last_hotel"] == "Grand Vilnius"
    assert props["next_checkin_date"] == "2027-09-10"
    assert props["last_checkout_date"] == "2027-09-13"


# --- monitoring ended payload ------------------------------------------------

def test_monitoring_ended_names_the_stay(db_session, monkeypatch, klaviyo_calls):
    from app.scheduler.worker import process_job

    session, _ = db_session
    user = models.User(email="t@example.com", hashed_password=None)
    session.add(user)
    session.commit()
    job = _job(1, JobStatus.active.value, date(2026, 1, 2), date(2026, 1, 4), "Old Stay")
    job.id = None
    job.user_id = user.id
    job.city = "Riga"
    session.add(job)
    session.commit()

    events = []
    monkeypatch.setattr(klaviyo, "emit_event", lambda n, e, p: (events.append((n, e, p)), True)[1])
    process_job(job, source=None, db=session, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
    name, email, props = events[0]
    assert name == klaviyo.EVENT_MONITORING_ENDED
    assert props["city"] == "Riga"
    assert props["check_in"] == "2026-01-02"
    assert props["check_out"] == "2026-01-04"
    assert job.status == JobStatus.expired.value
    # and the profile mirror now says the watch ended
    imports = [c for c in klaviyo_calls if "profile-import" in c["url"]]
    assert imports and imports[-1]["payload"]["data"]["attributes"]["properties"]["watch_status"] == "ended"
