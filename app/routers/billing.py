"""Stripe billing (§11): start a subscription checkout and consume webhooks that
map subscription status → User.plan_status. Also the server half of Meta ad
measurement: a browser-event relay for the Conversions API, and the Purchase
event fired off the Stripe webhook."""

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..enums import PlanStatus
from ..services import meta_capi, stripe_service
from .auth import _client_ip

logger = logging.getLogger("hotelsave.billing")
router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout")
def create_checkout(
    success_url: str,
    cancel_url: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    try:
        url = stripe_service.create_checkout_session(
            current_user.email, success_url, cancel_url
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return {"checkout_url": url}


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    """Map subscription lifecycle onto plan_status. Monitoring is paused for
    past_due/canceled by MonitoringJob queries that check User.can_monitor."""
    payload = await request.body()
    try:
        event = stripe_service.verify_webhook(payload, stripe_signature)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception:  # invalid signature / payload
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook")

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        # Payment Link checkout: nothing forces signup first, so the session's
        # email is the join key. Save the customer id so later subscription
        # events (which carry no email) still find this user.
        customer_id = obj.get("customer")
        email = _session_email(obj)
        user = _find_user(db, customer_id, email)
        if user is None and email:
            # A buyer without an account: create one so the payment is never
            # orphaned. The magic-link flow makes it claimable only by the
            # owner of that email address.
            user = models.User(email=email)
            db.add(user)
            logger.info("Checkout from unknown email %s — account created", email)
        if user:
            if customer_id:
                user.stripe_customer_id = customer_id
            user.plan_status = PlanStatus.pro.value
            db.commit()
            logger.info("Checkout completed: %s -> pro", user.email)

            # Server-side Purchase for Meta, AFTER plan-sync so measurement can
            # never get in the way of entitlement. Deduplicated against the
            # browser pixel by event_id = the Checkout Session id; value is in
            # currency units (49.00), not Stripe's cents. Never fails the
            # webhook: send_event swallows its own errors.
            amount = obj.get("amount_total")
            value = round(amount / 100, 2) if isinstance(amount, (int, float)) else 49.00
            currency = (obj.get("currency") or "usd").upper()
            meta_capi.send_event(
                "Purchase",
                obj.get("id") or f"stripe-cus-{customer_id}",
                email=user.email,
                fbp=user.meta_fbp,
                fbc=user.meta_fbc,
                event_source_url=f"{settings.site_base_url}/pricing",
                custom_data={"value": value, "currency": currency, **meta_capi.CONTENT},
            )
        else:
            logger.warning("checkout.session.completed without email or known customer")

    elif event_type.startswith("customer.subscription."):
        customer_id = obj.get("customer")
        stripe_status = (
            "canceled" if event_type == "customer.subscription.deleted"
            else obj.get("status", "")
        )
        new_status = stripe_service.map_subscription_status(stripe_status)
        user = _find_user(db, customer_id, None)
        if user:
            user.plan_status = new_status.value
            db.commit()
            logger.info("Updated %s plan_status -> %s", user.email, new_status.value)
        else:
            logger.warning("%s for unknown customer %s", event_type, customer_id)

    return {"received": True}


def _session_email(obj: dict) -> str | None:
    """Checkout session's email, lowercased: customer_details.email first,
    then the legacy top-level customer_email."""
    details = obj.get("customer_details") or {}
    email = details.get("email") or obj.get("customer_email")
    return email.strip().lower() if email else None


# Browser events this relay will forward, with the custom_data the server
# vouches for (the browser's own numbers are never trusted).
_BROWSER_EVENTS: dict[str, dict | None] = {
    "InitiateCheckout": {"value": 49.00, "currency": "USD", **meta_capi.CONTENT},
    "CompleteRegistration": None,
}


@router.post("/meta/event", status_code=status.HTTP_204_NO_CONTENT)
async def meta_browser_event(request: Request, db: Session = Depends(get_db)) -> Response:
    """Companion endpoint for the browser pixel: receives {event_name, event_id,
    event_source_url, fbp, fbc, email?} and forwards it to the Conversions API
    with the same event_id, so Meta deduplicates the pair. When an email is
    given, the _fbp/_fbc cookies are remembered on the account so the webhook's
    Purchase event can carry them.

    Sent with navigator.sendBeacon, which can only POST CORS-safelisted content
    types without a preflight — so the body is parsed as JSON regardless of its
    Content-Type, and the answer is always 204 (fire-and-forget)."""
    try:
        payload = json.loads((await request.body()) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError
    except Exception:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    event_name = payload.get("event_name")
    event_id = str(payload.get("event_id") or "").strip()[:64]
    if event_name not in _BROWSER_EVENTS or not event_id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def clip(key: str, limit: int) -> str | None:
        v = payload.get(key)
        v = str(v).strip()[:limit] if v else ""
        return v or None

    email = clip("email", 320)
    email = email.lower() if email else None
    fbp = clip("fbp", 128)
    fbc = clip("fbc", 512)

    if email and (fbp or fbc):
        user = db.scalar(
            select(models.User).where(func.lower(models.User.email) == email)
        )
        if user:
            if fbp:
                user.meta_fbp = fbp
            if fbc:
                user.meta_fbc = fbc
            db.commit()

    meta_capi.send_event(
        event_name,
        event_id,
        email=email,
        fbp=fbp,
        fbc=fbc,
        event_source_url=clip("event_source_url", 1024),
        custom_data=_BROWSER_EVENTS[event_name],
        client_ip=_client_ip(request),
        client_user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _find_user(
    db: Session, customer_id: str | None, email: str | None
) -> models.User | None:
    user = None
    if customer_id:
        user = db.scalar(
            select(models.User).where(models.User.stripe_customer_id == customer_id)
        )
    if user is None and email:
        user = db.scalar(
            select(models.User).where(func.lower(models.User.email) == email)
        )
    return user
