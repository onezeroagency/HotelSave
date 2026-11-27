"""Stripe billing (§11): start a subscription checkout and consume webhooks that
map subscription status → User.plan_status."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..enums import PlanStatus
from ..services import stripe_service

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
