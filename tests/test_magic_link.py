"""Passwordless magic links: signup, sign-in, single use, expiry, consent,
rate limits — and that password login keeps working beside it."""

import pytest
from sqlalchemy import select

from app import models
from app.config import settings
from app.services import mailer
from app.services.ratelimit import magic_link_limiter

EMAIL = "magic@example.com"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    magic_link_limiter.reset()
    yield
    magic_link_limiter.reset()


@pytest.fixture()
def outbox(monkeypatch):
    sent = []

    def fake_send(to, subject, html_body, text_body):
        sent.append({"to": to, "subject": subject, "html": html_body, "text": text_body})
        return True

    monkeypatch.setattr(mailer, "send_email", fake_send)
    return sent


def _request_link(client, email=EMAIL, **extra):
    return client.post("/auth/magic-link", json={"email": email, **extra})


def _token_from(outbox):
    assert outbox, "no magic-link email sent"
    text = outbox[-1]["text"]
    assert "save@myroomwatch.com" in text  # forwarding instructions ride along
    marker = "verify?token="
    start = text.index(marker) + len(marker)
    end = start
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def _verify(client, token):
    return client.get(f"/auth/magic-link/verify?token={token}", follow_redirects=False)


def _fragment_token(resp):
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith(f"{settings.site_base_url}/dashboard.html#token=")
    # The fragment may carry flags after the token (e.g. &new=1 on a brand-new
    # account) — the dashboard's parser stops at &, and so does this one.
    return location.split("#token=", 1)[1].split("&", 1)[0]


def test_new_user_magic_signup_end_to_end(client, db_session, outbox):
    r = _request_link(client)
    assert r.status_code == 202

    session, _ = db_session
    user = session.scalar(select(models.User).where(models.User.email == EMAIL))
    assert user is not None
    assert user.hashed_password is None  # truly passwordless
    assert outbox[-1]["to"] == EMAIL

    session_token = _fragment_token(_verify(client, _token_from(outbox)))
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_existing_password_user_can_magic_sign_in(client, outbox):
    r = client.post("/auth/register", json={"email": EMAIL, "password": "password-11"})
    assert r.status_code == 201

    _request_link(client)
    session_token = _fragment_token(_verify(client, _token_from(outbox)))
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {session_token}"}
    ).status_code == 200

    # The password path is untouched.
    assert client.post(
        "/auth/login", data={"username": EMAIL, "password": "password-11"}
    ).status_code == 200


def test_token_is_single_use(client, outbox):
    _request_link(client)
    token = _token_from(outbox)
    assert _verify(client, token).status_code == 303
    second = _verify(client, token)
    assert second.status_code == 303
    assert second.headers["location"].endswith("dashboard.html?magic=invalid")


def test_new_link_invalidates_the_old_one(client, outbox):
    _request_link(client)
    first = _token_from(outbox)
    _request_link(client)
    second = _token_from(outbox)
    assert _verify(client, first).headers["location"].endswith("?magic=invalid")
    assert "#token=" in _verify(client, second).headers["location"]


def test_expired_and_garbage_tokens_rejected(client, outbox, monkeypatch):
    monkeypatch.setattr(settings, "magic_link_expire_minutes", -1)
    _request_link(client)
    expired = _token_from(outbox)
    assert _verify(client, expired).headers["location"].endswith("?magic=invalid")
    assert _verify(client, "not-a-jwt").headers["location"].endswith("?magic=invalid")


def test_marketing_opt_in_is_stored_with_timestamp(client, db_session, outbox):
    _request_link(client, marketing_opt_in=True)
    session, _ = db_session
    user = session.scalar(select(models.User).where(models.User.email == EMAIL))
    assert user.marketing_opt_in is True
    assert user.marketing_opt_in_at is not None

    # An unticked box on a later request is NOT a withdrawal.
    _request_link(client)
    session.refresh(user)
    assert user.marketing_opt_in is True


def test_marketing_default_is_off(client, db_session, outbox):
    _request_link(client)
    session, _ = db_session
    user = session.scalar(select(models.User).where(models.User.email == EMAIL))
    assert user.marketing_opt_in is False
    assert user.marketing_opt_in_at is None


def test_later_opt_in_upgrades_existing_account(client, db_session, outbox):
    _request_link(client)
    _request_link(client, marketing_opt_in=True)
    session, _ = db_session
    user = session.scalar(select(models.User).where(models.User.email == EMAIL))
    assert user.marketing_opt_in is True and user.marketing_opt_in_at is not None


def test_per_email_rate_limit(client, outbox):
    for _ in range(settings.magic_link_email_limit):
        assert _request_link(client).status_code == 202
    assert _request_link(client).status_code == 429


def test_per_ip_rate_limit(client, outbox):
    n = 0
    for i in range(settings.magic_link_ip_limit):
        # Different addresses so only the IP window fills.
        assert _request_link(client, email=f"u{i}@example.com").status_code == 202
        n += 1
    assert _request_link(client, email="straw@example.com").status_code == 429
    assert n == settings.magic_link_ip_limit


def test_passwordless_account_cannot_password_login(client, outbox):
    _request_link(client)
    r = client.post("/auth/login", data={"username": EMAIL, "password": "anything-8"})
    assert r.status_code == 401


def test_magic_token_is_not_a_session_token(client, outbox):
    _request_link(client)
    magic = _token_from(outbox)
    assert client.get(
        "/auth/me", headers={"Authorization": f"Bearer {magic}"}
    ).status_code == 401
