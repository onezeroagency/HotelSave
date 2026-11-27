"""Auth: passwordless magic links, email/password login, register, and /me."""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..security import (
    create_access_token,
    create_magic_token,
    create_reset_token,
    hash_password,
    magic_token_is_new_account,
    verify_magic_token,
    verify_password,
    verify_reset_token,
)
from ..services import klaviyo, mailer
from ..services.ratelimit import magic_link_limiter

logger = logging.getLogger("hotelsave.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)) -> models.User:
    exists = db.scalar(select(models.User).where(models.User.email == payload.email))
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = models.User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Signup is the start of the activation path: the account is useless until a
    # booking is forwarded, so tell Klaviyo immediately and let the welcome flow
    # ask for one. The account already exists at this point — a marketing-side
    # failure must never turn a successful signup into an error for the user.
    try:
        klaviyo.emit_event(
            klaviyo.EVENT_ACCOUNT_CREATED,
            user.email,
            {
                "signup_source": "web",
                "forward_to": "save@myroomwatch.com",
                "marketing_opt_in": user.marketing_opt_in,
                "marketing_opt_in_at": None,
            },
        )
        klaviyo.register_signup(user.email, user.marketing_opt_in, user.marketing_opt_in_at)
    except Exception:  # pragma: no cover - defensive; emit_event handles HTTP errors itself
        logger.exception("Account Created event failed for %s — signup still succeeded", user.email)
    return user


def _client_ip(request: Request) -> str:
    # Render terminates TLS in front of us; the client is the first hop in
    # X-Forwarded-For. Fall back to the socket peer for local dev/tests.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/magic-link", status_code=status.HTTP_202_ACCEPTED)
def request_magic_link(
    payload: schemas.MagicLinkRequest, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Passwordless signup AND sign-in: one endpoint, one email.

    Unknown address -> the account is created on the spot (no password).
    Known address -> a fresh link; issuing it invalidates any earlier one."""
    window = settings.magic_link_window_seconds
    if not magic_link_limiter.allow(
        f"ip:{_client_ip(request)}", settings.magic_link_ip_limit, window
    ) or not magic_link_limiter.allow(
        f"email:{payload.email.lower()}", settings.magic_link_email_limit, window
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in links requested. Please try again in a few minutes.",
        )

    user = db.scalar(select(models.User).where(models.User.email == payload.email))
    created = user is None
    if created:
        user = models.User(
            email=payload.email,
            hashed_password=None,
            marketing_opt_in=payload.marketing_opt_in,
            marketing_opt_in_at=datetime.now(timezone.utc)
            if payload.marketing_opt_in
            else None,
        )
        db.add(user)
    newly_opted_in = False
    if not created and payload.marketing_opt_in and not user.marketing_opt_in:
        # Consent can be granted here; it is only ever withdrawn explicitly
        # (an unticked box on a later sign-in is not a withdrawal).
        user.marketing_opt_in = True
        user.marketing_opt_in_at = datetime.now(timezone.utc)
        newly_opted_in = True

    user.magic_nonce = secrets.token_urlsafe(24)
    db.commit()
    db.refresh(user)

    token = create_magic_token(user.id, user.magic_nonce, new_account=created)
    link = f"{settings.api_base_url}/auth/magic-link/verify?token={token}"
    subject, html, text = mailer.magic_link_email(link, settings.magic_link_expire_minutes)
    if not mailer.send_email(user.email, subject, html, text):
        # The user is told to check their inbox either way; a dropped send is
        # a silent dead end, so make it loud in the logs.
        logger.error("Magic link email NOT delivered for %s", user.email)

    try:
        if created:
            klaviyo.emit_event(
                klaviyo.EVENT_ACCOUNT_CREATED,
                user.email,
                {
                    "signup_source": "web-magic-link",
                    "forward_to": "save@myroomwatch.com",
                    "marketing_opt_in": user.marketing_opt_in,
                    "marketing_opt_in_at": user.marketing_opt_in_at.isoformat()
                    if user.marketing_opt_in_at
                    else None,
                },
            )
            klaviyo.register_signup(user.email, user.marketing_opt_in, user.marketing_opt_in_at)
        elif newly_opted_in:
            # Consent arrived after the account existed: record it on the
            # profile and subscribe, exactly as a fresh opt-in would have.
            klaviyo.upsert_profile(
                user.email,
                {
                    "marketing_opt_in": True,
                    "marketing_opt_in_at": user.marketing_opt_in_at.isoformat(),
                },
            )
            klaviyo.subscribe_email_marketing(user.email)
    except Exception:  # pragma: no cover - the klaviyo client handles HTTP errors itself
        logger.exception("Klaviyo signup sync failed for %s — signup still succeeded", user.email)

    return {"detail": "Check your email for a sign-in link."}


@router.get("/magic-link/verify")
def verify_magic_link(token: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """Consume a magic link: rotate the nonce (single use), mint the normal
    session JWT, and land the user in the dashboard. The JWT travels in the
    URL fragment so it never reaches server logs or Referer headers."""

    def lookup(user_id: str) -> str | None:
        u = db.get(models.User, int(user_id))
        return u.magic_nonce if u else None

    user_id = verify_magic_token(token, lookup)
    if user_id is None:
        return RedirectResponse(
            f"{settings.site_base_url}/dashboard.html?magic=invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    user = db.get(models.User, int(user_id))
    user.magic_nonce = None  # consumed — the same token can never verify again
    db.commit()
    session_token = create_access_token(user.id)
    # &new=1 marks the first sign-in of an account this link's request created,
    # so the dashboard fires its one-time registration event. It rides in the
    # fragment with the token, out of server logs and Referer headers.
    new_flag = "&new=1" if magic_token_is_new_account(token) else ""
    return RedirectResponse(
        f"{settings.site_base_url}/dashboard.html#token={session_token}{new_flag}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/login", response_model=schemas.Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> schemas.Token:
    # OAuth2 form uses `username`; we treat it as the email. Passwordless
    # accounts have no hash at all — they fail here cleanly and sign in by
    # magic link instead.
    user = db.scalar(select(models.User).where(models.User.email == form.username))
    if not user or not user.hashed_password or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return schemas.Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=schemas.UserRead)
def me(current_user: models.User = Depends(get_current_user)) -> models.User:
    return current_user


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: schemas.PasswordForgot, db: Session = Depends(get_db)) -> dict:
    """Email a password-reset link via Klaviyo (backend detects, Klaviyo delivers).

    The response is identical whether or not the address has an account —
    this endpoint is unauthenticated, so it must not confirm which emails
    are registered."""
    user = db.scalar(select(models.User).where(models.User.email == payload.email))
    if user:
        token = create_reset_token(user.id, user.hashed_password)
        reset_url = f"{settings.site_base_url}/reset.html?token={token}"
        delivered = klaviyo.emit_event(
            klaviyo.EVENT_PASSWORD_RESET,
            user.email,
            {
                "reset_url": reset_url,
                "expires_minutes": settings.reset_token_expire_minutes,
            },
        )
        if not delivered:
            # The user is told to check their inbox either way, so a dropped
            # event is a silent dead end — make it loud in the logs.
            logger.error("Password reset email NOT delivered for %s", user.email)
    return {"detail": "If that email has an account, we've sent a reset link."}


@router.post("/reset-password")
def reset_password(payload: schemas.PasswordReset, db: Session = Depends(get_db)) -> dict:
    def lookup(user_id: str) -> str | None:
        u = db.get(models.User, int(user_id))
        return u.hashed_password if u else None

    user_id = verify_reset_token(payload.token, lookup)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        )
    user = db.get(models.User, int(user_id))
    user.hashed_password = hash_password(payload.password)
    db.commit()
    return {"detail": "Password updated. You can sign in now."}
