"""Password hashing and JWT access tokens."""

import hashlib
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str | int, expires_minutes: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": str(subject), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> str | None:
    """Return the subject (user id) or None if the token is invalid/expired.

    Only plain access tokens pass: a password-reset token carries a `purpose`
    claim and is rejected here, so a reset link can never be used as a login."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    if payload.get("purpose") is not None:
        return None
    return payload.get("sub")


# --- password reset ---
#
# Reset tokens are stateless: no table, no migration, nothing to clean up.
# Single use is enforced by binding the token to a fingerprint of the CURRENT
# password hash — the moment the password changes (via this token or any other
# way), every token issued before it stops matching.

_RESET_PURPOSE = "password-reset"


def _password_fingerprint(hashed_password: str) -> str:
    # Never embed the bcrypt hash itself in a token the user's inbox holds.
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:16]


def create_reset_token(user_id: int, hashed_password: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.reset_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "purpose": _RESET_PURPOSE,
        "pwf": _password_fingerprint(hashed_password),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def verify_reset_token(token: str, hashed_password_lookup) -> str | None:
    """Return the user id the token belongs to, or None.

    `hashed_password_lookup` maps a user id (str) to that user's current
    hashed password, or None when the user doesn't exist — the caller owns
    the DB session, this module stays storage-free."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    if payload.get("purpose") != _RESET_PURPOSE:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    hashed = hashed_password_lookup(user_id)
    if hashed is None or payload.get("pwf") != _password_fingerprint(hashed):
        return None
    return user_id
