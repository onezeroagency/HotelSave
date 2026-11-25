"""Forgot/reset password: stateless single-use tokens, Klaviyo delivery."""

from app.config import settings
from app.services import klaviyo

EMAIL = "resetter@example.com"
OLD_PW = "original-pw-1"
NEW_PW = "brand-new-pw-2"


def _register(client, email=EMAIL, password=OLD_PW):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201


def _capture_events(monkeypatch):
    events = []

    def fake_emit(event_name, email, properties):
        events.append((event_name, email, properties))
        return True

    monkeypatch.setattr(klaviyo, "emit_event", fake_emit)
    return events


def _token_from(events):
    assert events, "no Klaviyo event emitted"
    name, _email, props = events[-1]
    assert name == klaviyo.EVENT_PASSWORD_RESET
    url = props["reset_url"]
    assert url.startswith(f"{settings.site_base_url}/reset.html?token=")
    return url.split("token=", 1)[1]


def test_full_reset_flow(client, monkeypatch):
    _register(client)
    events = _capture_events(monkeypatch)

    r = client.post("/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 202
    token = _token_from(events)
    assert events[-1][1] == EMAIL
    assert events[-1][2]["expires_minutes"] == settings.reset_token_expire_minutes

    r = client.post("/auth/reset-password", json={"token": token, "password": NEW_PW})
    assert r.status_code == 200

    # Old password out, new password in.
    r = client.post("/auth/login", data={"username": EMAIL, "password": OLD_PW})
    assert r.status_code == 401
    r = client.post("/auth/login", data={"username": EMAIL, "password": NEW_PW})
    assert r.status_code == 200


def test_token_is_single_use(client, monkeypatch):
    _register(client)
    events = _capture_events(monkeypatch)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_from(events)

    assert client.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PW}
    ).status_code == 200
    # The hash changed, so the same token no longer matches its fingerprint.
    r = client.post("/auth/reset-password", json={"token": token, "password": "third-pw-33"})
    assert r.status_code == 400
    # And the second attempt changed nothing.
    assert client.post(
        "/auth/login", data={"username": EMAIL, "password": NEW_PW}
    ).status_code == 200


def test_unknown_email_gets_same_answer_and_no_event(client, monkeypatch):
    events = _capture_events(monkeypatch)
    r = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 202
    assert events == []
    # Same body as the known-email case — no account enumeration.
    assert "reset link" in r.json()["detail"]


def test_garbage_and_expired_tokens_fail(client, monkeypatch):
    _register(client)
    events = _capture_events(monkeypatch)

    r = client.post("/auth/reset-password", json={"token": "not-a-jwt", "password": NEW_PW})
    assert r.status_code == 400

    monkeypatch.setattr(settings, "reset_token_expire_minutes", -1)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    expired = _token_from(events)
    r = client.post("/auth/reset-password", json={"token": expired, "password": NEW_PW})
    assert r.status_code == 400


def test_tokens_do_not_cross_purposes(client, monkeypatch):
    _register(client)
    events = _capture_events(monkeypatch)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    reset_token = _token_from(events)

    # A reset token is not a login: /auth/me must reject it.
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {reset_token}"})
    assert r.status_code == 401

    # An access token is not a reset token: it lacks the purpose claim.
    login = client.post("/auth/login", data={"username": EMAIL, "password": OLD_PW})
    access = login.json()["access_token"]
    r = client.post("/auth/reset-password", json={"token": access, "password": NEW_PW})
    assert r.status_code == 400


def test_reset_rejects_passwords_registration_would(client, monkeypatch):
    _register(client)
    events = _capture_events(monkeypatch)
    client.post("/auth/forgot-password", json={"email": EMAIL})
    token = _token_from(events)
    r = client.post("/auth/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 422
