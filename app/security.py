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


# --- magic login links ---
#
# Same stateless-JWT shape as reset tokens, but single use comes from a
# rotating per-user nonce instead of the password hash (a magic login changes
# nothing about the account, so it needs its own thing to rotate).

_MAGIC_PURPOSE = "magic-login"


def create_magic_token(user_id: int, nonce: str, new_account: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.magic_link_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "purpose": _MAGIC_PURPOSE,
        "nonce": nonce,
    }
    if new_account:
        # Set only when this magic-link REQUEST created the account, so the
        # dashboard can fire its one-time registration event. Repeat sign-ins,
        # and accounts created any other way (e.g. by the Stripe webhook),
        # never carry it.
        payload["new"] = True
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def magic_token_is_new_account(token: str) -> bool:
    """Read the `new` claim of an (already verified) magic token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return False
    return payload.get("new") is True


def verify_magic_token(token: str, nonce_lookup) -> str | None:
    """Return the user id, or None. `nonce_lookup` maps a user id (str) to
    that user's CURRENT magic nonce (None when the user doesn't exist or no
    link is outstanding)."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    if payload.get("purpose") != _MAGIC_PURPOSE:
        return None
    user_id = payload.get("sub")
    token_nonce = payload.get("nonce")
    if not user_id or not token_nonce:
        return None
    current = nonce_lookup(user_id)
    if current is None or token_nonce != current:
        return None
    return user_id


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
