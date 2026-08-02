"""Stripe subscription glue (§11). Kept thin: the app maps Stripe subscription
status onto User.plan_status and pauses monitoring for past_due/canceled.

Real keys go in .env. Until then create_checkout_session raises a clear error so
the wiring is obvious but nothing pretends to charge anyone."""

import logging

import stripe

from ..config import settings
from ..enums import PlanStatus

logger = logging.getLogger("hotelsave.stripe")

if settings.stripe_secret_key:
    stripe.api_key = settings.stripe_secret_key


# Stripe subscription.status  ->  User.plan_status  (§11)
_STATUS_MAP: dict[str, PlanStatus] = {
    "trialing": PlanStatus.trialing,
    "active": PlanStatus.active,
    "past_due": PlanStatus.past_due,
    "unpaid": PlanStatus.past_due,
    "canceled": PlanStatus.canceled,
    "incomplete": PlanStatus.trialing,
    "incomplete_expired": PlanStatus.canceled,
    "paused": PlanStatus.canceled,
}


def map_subscription_status(stripe_status: str) -> PlanStatus:
    return _STATUS_MAP.get(stripe_status, PlanStatus.canceled)


def create_checkout_session(customer_email: str, success_url: str, cancel_url: str) -> str:
    """Create a subscription Checkout session, return its URL.

    Funnel (§11): first booking monitored free, then gate further monitoring."""
    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise RuntimeError(
            "Stripe not configured — set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in .env"
        )
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=customer_email,
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def verify_webhook(payload: bytes, signature: str) -> dict:
    """Verify a Stripe webhook signature and return the parsed event."""
    if not settings.stripe_webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")
    return stripe.Webhook.construct_event(
        payload, signature, settings.stripe_webhook_secret
    )
