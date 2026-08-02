"""Stripe billing (§11): start a subscription checkout and consume webhooks that
map subscription status → User.plan_status."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_current_user
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

    if event_type.startswith("customer.subscription."):
        customer_id = obj.get("customer")
        new_status = stripe_service.map_subscription_status(obj.get("status", ""))
        user = db.scalar(
            select(models.User).where(models.User.stripe_customer_id == customer_id)
        )
        if user:
            user.plan_status = new_status.value
            db.commit()
            logger.info("Updated %s plan_status -> %s", user.email, new_status.value)

    return {"received": True}
