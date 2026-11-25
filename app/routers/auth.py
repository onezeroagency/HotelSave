"""Email/password auth: register, login (OAuth2 password flow), and /me."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..security import (
    create_access_token,
    create_reset_token,
    hash_password,
    verify_password,
    verify_reset_token,
)
from ..services import klaviyo

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
            {"signup_source": "web", "forward_to": "save@myroomwatch.com"},
        )
    except Exception:  # pragma: no cover - defensive; emit_event handles HTTP errors itself
        logger.exception("Account Created event failed for %s — signup still succeeded", user.email)
    return user


@router.post("/login", response_model=schemas.Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> schemas.Token:
    # OAuth2 form uses `username`; we treat it as the email.
    user = db.scalar(select(models.User).where(models.User.email == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
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
