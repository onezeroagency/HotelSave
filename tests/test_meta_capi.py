"""Meta Pixel / Conversions API wiring: the webhook's Purchase event, the
browser-event relay, and the guarantees around it — dollars not cents, dedupe
on the Stripe session id, the token never logged, and plan-sync/caps intact."""

import hashlib
import logging

import pytest

from app import models
from app.config import settings
from app.services import meta_capi


class _FakeResponse:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):  # satisfies other httpx callers (Klaviyo)
        return None

    def json(self):
        return {}


@pytest.fixture()
def capi_calls(monkeypatch):
    """Capture outgoing CAPI posts; pretend Meta accepted them.

    meta_capi shares the global httpx module, so the patch sees every
    httpx.post in the app (e.g. Klaviyo's) — those are answered with a bland
    200 and only graph.facebook.com calls are recorded."""
    calls: list[dict] = []

    def fake_post(url, *args, json=None, **kwargs):
        if "graph.facebook.com" in url:
            calls.append({"url": url, "json": json})
        return _FakeResponse()

    monkeypatch.setattr("app.services.meta_capi.httpx.post", fake_post)
    monkeypatch.setattr(settings, "meta_capi_token", "capi_secret_token_123")
    return calls


def _mock_webhook(monkeypatch, event: dict) -> None:
    monkeypatch.setattr(
        "app.services.stripe_service.verify_webhook", lambda payload, sig: event
    )


def _checkout_event(email="buyer@example.com", amount_total=4900):
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_abc123",
            "customer": "cus_capi1",
            "customer_details": {"email": email},
            "amount_total": amount_total,
            "currency": "usd",
        }},
    }


def test_webhook_purchase_sends_dollars_and_session_event_id(
    auth_client, capi_calls, monkeypatch
):
    _mock_webhook(monkeypatch, _checkout_event(email="traveler@example.com"))
    assert auth_client.post("/billing/webhook", json={}).status_code == 200

    # Plan-sync ran first and still works.
    assert auth_client.get("/auth/me").json()["plan_status"] == "pro"

    assert len(capi_calls) == 1
    body = capi_calls[0]["json"]
    event = body["data"][0]
    assert event["event_name"] == "Purchase"
    assert event["event_id"] == "cs_test_abc123"  # dedupe key = session id
    assert event["custom_data"]["value"] == 49.00  # dollars, not 4900 cents
    assert event["custom_data"]["currency"] == "USD"
    assert event["custom_data"]["content_ids"] == ["myroomwatch-founding"]
    assert event["custom_data"]["content_type"] == "product"
    assert event["custom_data"]["content_name"] == "myroomwatch-founding"
    expected_em = hashlib.sha256(b"traveler@example.com").hexdigest()
    assert event["user_data"]["em"] == [expected_em]
    assert settings.meta_pixel_id in capi_calls[0]["url"]


def test_capi_failure_never_fails_webhook_or_logs_token(
    auth_client, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "meta_capi_token", "capi_secret_token_123")

    def exploding_post(url, *args, **kwargs):
        if "graph.facebook.com" not in url:
            return _FakeResponse()
        # Worst case: Meta echoes the whole request back in an error.
        return _FakeResponse(status_code=400, text="bad token: capi_secret_token_123")

    monkeypatch.setattr("app.services.meta_capi.httpx.post", exploding_post)
    _mock_webhook(monkeypatch, _checkout_event(email="traveler@example.com"))

    with caplog.at_level(logging.INFO):
        assert auth_client.post("/billing/webhook", json={}).status_code == 200
    # Plan-sync unaffected by the CAPI failure.
    assert auth_client.get("/auth/me").json()["plan_status"] == "pro"
    # The failure is logged, the token never is.
    assert "Meta CAPI Purchase failed" in caplog.text
    assert "capi_secret_token_123" not in caplog.text

    # A raising transport is equally harmless.
    def raising_post(url, *args, **kwargs):
        if "graph.facebook.com" not in url:
            return _FakeResponse()
        raise RuntimeError("boom capi_secret_token_123")

    monkeypatch.setattr("app.services.meta_capi.httpx.post", raising_post)
    _mock_webhook(monkeypatch, {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_other",
            "customer": "cus_capi1",
            "customer_details": {"email": "traveler@example.com"},
            "amount_total": 4900,
            "currency": "usd",
        }},
    })
    with caplog.at_level(logging.INFO):
        assert auth_client.post("/billing/webhook", json={}).status_code == 200
    assert "capi_secret_token_123" not in caplog.text


def test_browser_event_relay_stores_fbp_and_forwards(
    auth_client, db_session, capi_calls
):
    r = auth_client.post(
        "/billing/meta/event",
        content=b'{"event_name":"InitiateCheckout","event_id":"evt-1",'
        b'"event_source_url":"https://www.myroomwatch.com/pricing",'
        b'"fbp":"fb.1.123.456","fbc":"fb.1.123.AbC","email":"Traveler@Example.com"}',
        headers={"content-type": "text/plain"},  # sendBeacon-style body
    )
    assert r.status_code == 204

    session, _ = db_session
    user = session.query(models.User).filter_by(email="traveler@example.com").one()
    assert user.meta_fbp == "fb.1.123.456"
    assert user.meta_fbc == "fb.1.123.AbC"

    event = capi_calls[0]["json"]["data"][0]
    assert event["event_name"] == "InitiateCheckout"
    assert event["event_id"] == "evt-1"
    assert event["custom_data"]["value"] == 49.00
    assert event["user_data"]["fbp"] == "fb.1.123.456"

    # Garbage and unknown events are swallowed silently (fire-and-forget).
    assert auth_client.post("/billing/meta/event", content=b"not json").status_code == 204
    assert auth_client.post(
        "/billing/meta/event", content=b'{"event_name":"Hack","event_id":"x"}'
    ).status_code == 204
    assert len(capi_calls) == 1  # neither reached Meta


def test_purchase_capi_uses_stored_fbp(auth_client, capi_calls, monkeypatch):
    auth_client.post(
        "/billing/meta/event",
        content=b'{"event_name":"InitiateCheckout","event_id":"evt-2",'
        b'"fbp":"fb.1.9.9","email":"traveler@example.com"}',
    )
    _mock_webhook(monkeypatch, _checkout_event(email="traveler@example.com"))
    assert auth_client.post("/billing/webhook", json={}).status_code == 200
    purchase = capi_calls[-1]["json"]["data"][0]
    assert purchase["event_name"] == "Purchase"
    assert purchase["user_data"]["fbp"] == "fb.1.9.9"


def test_free_cap_still_enforced_with_capi_wired(auth_client, capi_calls):
    payload = {
        "hotel_name_raw": "Hotel Kaunas", "city": "Vilnius", "country": "LT",
        "check_in": "2026-09-10", "check_out": "2026-09-13",
        "adults": 2, "children": 0, "original_price": "420.00",
        "currency": "EUR", "refundable": True,
        "cancellation_deadline": "2026-09-07T23:59:00",
    }
    assert auth_client.post("/jobs", json=payload).status_code == 201
    assert auth_client.post("/jobs", json=payload).status_code == 402
