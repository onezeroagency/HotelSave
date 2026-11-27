"""Meta Conversions API client — the server half of the Meta Pixel. Events are
deduplicated against the browser pixel by event_id (Purchase uses the Stripe
Checkout Session id; browser-initiated events bring their own UUID).

Fire-and-forget: a CAPI failure must never break checkout, signup or the
Stripe webhook, so send_event returns False instead of raising. Without a
token it dry-runs to the log, the same contract as the Klaviyo client.
The access token must never reach the logs."""

import hashlib
import logging
import time

import httpx

from ..config import settings

logger = logging.getLogger("hotelsave.meta")

# The one product we sell, as Meta content params.
CONTENT = {
    "content_ids": ["myroomwatch-founding"],
    "content_type": "product",
    "content_name": "myroomwatch-founding",
}


def hash_email(email: str) -> str:
    """Meta's normalization for the `em` field: trim, lowercase, SHA-256."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _scrub(text: str) -> str:
    """Strip the access token from anything headed for the logs."""
    if settings.meta_capi_token:
        text = text.replace(settings.meta_capi_token, "[redacted]")
    return text


def send_event(
    event_name: str,
    event_id: str,
    *,
    email: str | None = None,
    fbp: str | None = None,
    fbc: str | None = None,
    event_source_url: str | None = None,
    custom_data: dict | None = None,
    client_ip: str | None = None,
    client_user_agent: str | None = None,
) -> bool:
    user_data: dict = {}
    if email:
        user_data["em"] = [hash_email(email)]
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if client_user_agent:
        user_data["client_user_agent"] = client_user_agent

    event: dict = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": "website",
    }
    if event_source_url:
        event["event_source_url"] = event_source_url
    if user_data:
        event["user_data"] = user_data
    if custom_data:
        event["custom_data"] = custom_data

    if not settings.meta_capi_token:
        logger.info("[dry-run] Meta CAPI %s event_id=%s (no META_CAPI_TOKEN)", event_name, event_id)
        return False

    try:
        resp = httpx.post(
            f"{settings.meta_capi_base_url}/{settings.meta_pixel_id}/events",
            json={"data": [event], "access_token": settings.meta_capi_token},
            timeout=5.0,
        )
        if resp.status_code >= 400:
            logger.error(
                "Meta CAPI %s failed: HTTP %s %s",
                event_name, resp.status_code, _scrub(resp.text[:300]),
            )
            return False
        return True
    except Exception as exc:  # network errors, timeouts — never propagate
        logger.error("Meta CAPI %s failed: %s", event_name, _scrub(str(exc))[:300])
        return False
