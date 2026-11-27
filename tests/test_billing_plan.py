"""Plan gating (§11): the free tier watches 1 active booking, pro is unlimited,
and the Stripe webhook is what moves a user between the two."""

from app import models


def _job_payload(**overrides):
    payload = {
        "hotel_name_raw": "Hotel Kaunas",
        "city": "Vilnius",
        "country": "LT",
        "check_in": "2026-09-10",
        "check_out": "2026-09-13",
        "room_type_raw": "Superior Double, City View",
        "board_type": "BB",
        "adults": 2,
        "children": 0,
        "original_price": "420.00",
        "currency": "EUR",
        "original_ota": "Booking.com",
        "refundable": True,
        "cancellation_deadline": "2026-09-07T23:59:00",
    }
    payload.update(overrides)
    return payload


def _mock_webhook(monkeypatch, event: dict) -> None:
    """Bypass signature verification; the handler gets `event` directly."""
    monkeypatch.setattr(
        "app.services.stripe_service.verify_webhook", lambda payload, sig: event
    )


def test_free_cap_blocks_second_active_job(auth_client):
    assert auth_client.post("/jobs", json=_job_payload()).status_code == 201

    second = auth_client.post("/jobs", json=_job_payload(hotel_name_raw="Hotel Vilnius"))
    assert second.status_code == 402
    assert "/pricing" in second.json()["detail"]

    # Only ACTIVE bookings count: cancelling the first frees the slot.
    first_id = auth_client.get("/jobs").json()[0]["id"]
    assert auth_client.delete(f"/jobs/{first_id}").status_code == 204
    assert auth_client.post(
        "/jobs", json=_job_payload(hotel_name_raw="Hotel Vilnius")
    ).status_code == 201


def test_checkout_completed_upgrades_to_pro_and_unlocks(auth_client, monkeypatch):
    assert auth_client.post("/jobs", json=_job_payload()).status_code == 201
    assert auth_client.post("/jobs", json=_job_payload()).status_code == 402

    # Email match is case-insensitive, and the customer id is saved for later
    # subscription events (which carry no email).
    _mock_webhook(monkeypatch, {
        "type": "checkout.session.completed",
        "data": {"object": {
            "customer": "cus_test123",
            "customer_details": {"email": "Traveler@Example.COM"},
        }},
    })
    assert auth_client.post("/billing/webhook", json={}).status_code == 200

    me = auth_client.get("/auth/me").json()
    assert me["plan_status"] == "pro"
    assert auth_client.post("/jobs", json=_job_payload()).status_code == 201  # unlimited

    # Subscription dies → back to the free tier, matched by stripe_customer_id.
    _mock_webhook(monkeypatch, {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_test123", "status": "canceled"}},
    })
    assert auth_client.post("/billing/webhook", json={}).status_code == 200
    assert auth_client.get("/auth/me").json()["plan_status"] == "free"
    # Already over the free cap, so another watch is refused again.
    assert auth_client.post("/jobs", json=_job_payload()).status_code == 402


def test_checkout_from_unknown_email_creates_pro_account(client, db_session, monkeypatch):
    _mock_webhook(monkeypatch, {
        "type": "checkout.session.completed",
        "data": {"object": {
            "customer": "cus_new9",
            "customer_details": {"email": "buyer@example.com"},
        }},
    })
    assert client.post("/billing/webhook", json={}).status_code == 200

    session, _ = db_session
    user = session.query(models.User).filter_by(email="buyer@example.com").one()
    assert user.plan_status == "pro"
    assert user.stripe_customer_id == "cus_new9"
