"""Klaviyo events (§10). Principle: backend detects, Klaviyo delivers.

The backend never sends email directly — it emits events; flows own all copy,
timing, and lifecycle. Until KLAVIYO_API_KEY is set, events are logged so the
rest of the system can be exercised end-to-end without a live account."""

import logging

import httpx

from ..config import settings

logger = logging.getLogger("hotelsave.klaviyo")

KLAVIYO_BASE_URL = "https://a.klaviyo.com/api"
KLAVIYO_EVENTS_URL = f"{KLAVIYO_BASE_URL}/events/"
KLAVIYO_REVISION = "2024-10-15"

# Klaviyo account VAJcs5 ("MyRoomWatch"), IDs verified against the live
# account on 2026-11-26. New users go on the Signups list; RHXEeq ("Email
# List") is the generic Shopify-era list and must never receive our users.
LIST_SIGNUPS = "RZ4A3b"  # "MyRoomWatch Signups"

# Core event names the backend emits (§10). Flows trigger off these.
EVENT_ACCOUNT_CREATED = "Account Created"  # signup → welcome/activation flow (§10)
EVENT_BOOKING_MONITORED = "Booking Monitored"
EVENT_PRICE_DROP_FOUND = "Price Drop Found"
EVENT_DEADLINE_APPROACHING = "Deadline Approaching"
EVENT_MONITORING_ENDED = "Monitoring Ended"

# Account events — transactional, must reach the user regardless of consent.
EVENT_PASSWORD_RESET = "Password Reset Requested"  # carries reset_url + expiry

# Ingestion/onboarding events (§5, §6) — transactional prompts back to the user.
EVENT_PARSE_FAILED = "Parse Failed"  # forwarded confirmation we couldn't read
EVENT_SIGNUP_INVITE = "Forwarded Without Account"  # unknown sender → invite to sign up
EVENT_NEEDS_REFUNDABLE = "Refundable Confirmation Needed"  # non-refundable booking (§6a)
EVENT_PLAN_LIMIT_REACHED = "Plan Limit Reached"  # free tier already watching 1 booking (§11)
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


def _headers() -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {settings.klaviyo_api_key}",
        "revision": KLAVIYO_REVISION,
        "content-type": "application/json",
        "accept": "application/json",
    }


def _post(url: str, payload: dict, what: str) -> dict | None:
    """POST to Klaviyo; returns the parsed body ({} for 202-no-content), or
    None on failure. Dry-runs (and reports success) without an API key."""
    if not settings.klaviyo_api_key:
        logger.info("[klaviyo:dry-run] %s %s", what, payload)
        return {}
    try:
        resp = httpx.post(url, json=payload, headers=_headers(), timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError:  # pragma: no cover - network path
        logger.exception("Klaviyo %s failed", what)
        return None
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        return {}


def upsert_profile(email: str, properties: dict) -> str | None:
    """Create-or-update a profile's custom properties. Returns the profile id
    (empty string in dry-run), or None on failure."""
    body = _post(
        f"{KLAVIYO_BASE_URL}/profile-import/",
        {"data": {"type": "profile", "attributes": {"email": email, "properties": properties}}},
        f"profile upsert for {email}",
    )
    if body is None:
        return None
    return (body.get("data") or {}).get("id", "")


def add_to_signups_list(profile_id: str) -> bool:
    """Put a profile on the Signups list (membership only — NOT marketing
    consent; that is subscribe_email_marketing's job and needs opt-in)."""
    if not profile_id:  # dry-run upsert has no id to attach
        return True
    return _post(
        f"{KLAVIYO_BASE_URL}/lists/{LIST_SIGNUPS}/relationships/profiles/",
        {"data": [{"type": "profile", "id": profile_id}]},
        f"list add {profile_id}",
    ) is not None


def subscribe_email_marketing(email: str) -> bool:
    """Record email-marketing consent. Call ONLY when the user explicitly
    ticked the (default-off) marketing checkbox — never for plain signups."""
    payload = {
        "data": {
            "type": "profile-subscription-bulk-create-job",
            "attributes": {
                "custom_source": "MyRoomWatch signup checkbox",
                "profiles": {
                    "data": [
                        {
                            "type": "profile",
                            "attributes": {
                                "email": email,
                                "subscriptions": {
                                    "email": {"marketing": {"consent": "SUBSCRIBED"}}
                                },
                            },
                        }
                    ]
                },
            },
            "relationships": {
                "list": {"data": {"type": "list", "id": LIST_SIGNUPS}}
            },
        }
    }
    return _post(
        f"{KLAVIYO_BASE_URL}/profile-subscription-bulk-create-jobs/",
        payload,
        f"marketing subscribe for {email}",
    ) is not None


def register_signup(email: str, marketing_opt_in: bool, marketing_opt_in_at) -> None:
    """Everything Klaviyo needs to know about a new account, best-effort:
    profile with consent properties, Signups-list membership, and — only with
    explicit opt-in — email marketing consent. Failures are logged, never
    raised: marketing plumbing must not break a signup."""
    profile_id = upsert_profile(
        email,
        {
            "marketing_opt_in": marketing_opt_in,
            "marketing_opt_in_at": marketing_opt_in_at.isoformat()
            if marketing_opt_in_at
            else None,
        },
    )
    if profile_id is None:
        logger.error("Klaviyo profile upsert failed for %s", email)
        return
    if not add_to_signups_list(profile_id):
        logger.error("Klaviyo Signups-list add failed for %s", email)
    if marketing_opt_in and not subscribe_email_marketing(email):
        logger.error("Klaviyo marketing subscribe failed for %s", email)


def watch_profile_properties(jobs) -> dict:
    """The profile-level mirror of a user's watches, for segmentation.
    Pure function of the user's jobs; call upsert_profile with the result."""
    from ..enums import JobStatus  # local import to avoid a cycle at module load

    watching = {JobStatus.active.value, JobStatus.drop_found.value, JobStatus.deadline_soon.value}
    active = [j for j in jobs if j.status in watching]
    # Newest by id: ids are monotonic and always set, where created_at can come
    # back naive from SQLite and aware from Postgres.
    newest = max(jobs, key=lambda j: j.id or 0, default=None)
    future_checkins = [j.check_in for j in active if j.check_in]
    return {
        "watch_status": "watching" if active else ("ended" if jobs else "none"),
        "watch_count": len(jobs),
        "active_watch_count": len(active),
        "last_hotel": newest.hotel_name_raw if newest else None,
        "next_checkin_date": min(future_checkins).isoformat() if future_checkins else None,
        "last_checkout_date": max((j.check_out for j in jobs if j.check_out), default=None).isoformat()
        if any(j.check_out for j in jobs)
        else None,
    }


def sync_watch_profile(email: str, jobs) -> None:
    """Best-effort profile sync after a watch starts or ends."""
    if upsert_profile(email, watch_profile_properties(jobs)) is None:
        logger.error("Klaviyo watch-profile sync failed for %s", email)


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
