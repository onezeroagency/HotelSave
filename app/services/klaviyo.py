"""Klaviyo events (§10). Principle: backend detects, Klaviyo delivers.

The backend never sends email directly — it emits events; flows own all copy,
timing, and lifecycle. Until KLAVIYO_API_KEY is set, events are logged so the
rest of the system can be exercised end-to-end without a live account."""

import logging

import httpx

from ..config import settings

logger = logging.getLogger("hotelsave.klaviyo")

KLAVIYO_EVENTS_URL = "https://a.klaviyo.com/api/events/"
KLAVIYO_REVISION = "2024-10-15"

# Core event names the backend emits (§10). Flows trigger off these.
EVENT_BOOKING_MONITORED = "Booking Monitored"
EVENT_PRICE_DROP_FOUND = "Price Drop Found"
EVENT_DEADLINE_APPROACHING = "Deadline Approaching"
EVENT_MONITORING_ENDED = "Monitoring Ended"

# Ingestion/onboarding events (§5, §6) — transactional prompts back to the user.
EVENT_SIGNUP_INVITE = "Forwarded Without Account"  # unknown sender → invite to sign up
EVENT_NEEDS_REFUNDABLE = "Refundable Confirmation Needed"  # non-refundable booking (§6a)
EVENT_NEEDS_DEADLINE = "Cancellation Deadline Needed"  # deadline missing (§6a)
EVENT_HOTEL_RESOLUTION_NEEDED = "Hotel Resolution Needed"  # ambiguous hotel (§6b)


def booking_monitored_properties(job) -> dict:
    """Payload for the `Booking Monitored` event — shared by ingestion and the
    manual CRUD path so the event shape stays consistent."""
    return {
        "hotel": job.hotel_name_raw,
        "city": job.city,
        "check_in": job.check_in.isoformat(),
        "check_out": job.check_out.isoformat(),
        "original_price": float(job.original_price),
        "currency": job.currency,
        "cancellation_deadline": job.cancellation_deadline.isoformat()
        if job.cancellation_deadline
        else None,
    }


def emit_event(event_name: str, email: str, properties: dict) -> bool:
    """Push an event to Klaviyo (or log it if no key is configured).

    Returns True when the event was accepted (or deliberately dry-run), False
    when delivery failed. Callers that record "the user has been told" must gate
    that on the return value — marking an alert sent after a failed delivery
    silently loses it, and it is never retried.

    NOTE: these are transactional alerts (§10 gotcha) — they must reach the user
    regardless of marketing-consent status. Configure that on the Klaviyo side."""
    if not settings.klaviyo_api_key:
        # Dry-run is an intentional mode (local dev / no key yet), not a failure:
        # report success so callers don't retry forever.
        logger.info("[klaviyo:dry-run] %s -> %s %s", event_name, email, properties)
        return True

    payload = {
        "data": {
            "type": "event",
            "attributes": {
                "metric": {"data": {"type": "metric", "attributes": {"name": event_name}}},
                "profile": {"data": {"type": "profile", "attributes": {"email": email}}},
                "properties": properties,
            },
        }
    }
    headers = {
        "Authorization": f"Klaviyo-API-Key {settings.klaviyo_api_key}",
        "revision": KLAVIYO_REVISION,
        "content-type": "application/json",
        "accept": "application/json",
    }
    try:
        resp = httpx.post(KLAVIYO_EVENTS_URL, json=payload, headers=headers, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError:  # pragma: no cover - network path
        logger.exception("Failed to emit Klaviyo event %s for %s", event_name, email)
        return False
    return True
