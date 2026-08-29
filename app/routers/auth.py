"""Email/password auth: register, login (OAuth2 password flow), and /me."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user
from ..security import create_access_token, hash_password, verify_password
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
