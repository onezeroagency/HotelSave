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

# Event names the backend emits (§10). Flows trigger off these.
EVENT_BOOKING_MONITORED = "Booking Monitored"
EVENT_PRICE_DROP_FOUND = "Price Drop Found"
EVENT_DEADLINE_APPROACHING = "Deadline Approaching"
EVENT_MONITORING_ENDED = "Monitoring Ended"


def emit_event(event_name: str, email: str, properties: dict) -> None:
    """Push an event to Klaviyo (or log it if no key is configured).

    NOTE: these are transactional alerts (§10 gotcha) — they must reach the user
    regardless of marketing-consent status. Configure that on the Klaviyo side."""
    if not settings.klaviyo_api_key:
        logger.info("[klaviyo:dry-run] %s -> %s %s", event_name, email, properties)
        return

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
