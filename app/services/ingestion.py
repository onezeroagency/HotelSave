"""Ingestion orchestration (§5, §6): inbound email → parse → resolve → job.

Matches the sender to a user, parses the confirmation, applies the post-parse
guards (§6a), resolves the hotel_id (§6b), and creates a MonitoringJob in the
right state — active if everything's present, otherwise a pending_* holding
state with a prompt back to the user."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..enums import JobStatus
from . import klaviyo, lifecycle
from .email_inbound import InboundEmail
from .parser import ParsedBooking, get_parser
from .price_source import get_price_source


@dataclass
class IngestionResult:
    status: str  # unknown_sender | parse_failed | not_refundable | monitoring | pending_hotel | pending_info
    job_id: int | None = None


logger = logging.getLogger("hotelsave.ingestion")


def process_inbound_email(db: Session, inbound: InboundEmail) -> IngestionResult:
    user = db.scalar(select(models.User).where(models.User.email == inbound.from_email))
    if user is None:
        # Unknown sender → acquisition hook (§5): invite them to sign up.
        klaviyo.emit_event(
            klaviyo.EVENT_SIGNUP_INVITE, inbound.from_email, {"subject": inbound.subject}
        )
        return IngestionResult(status="unknown_sender")

    parsed = get_parser().parse(inbound.body)
    if parsed is None:
        logger.info("Could not parse email from %s", inbound.from_email)
        return IngestionResult(status="parse_failed")

    # Guard: only monitor refundable bookings (§6a, §7 rule 3).
    if parsed.refundable is not True:
        klaviyo.emit_event(
            klaviyo.EVENT_NEEDS_REFUNDABLE,
            user.email,
            {"hotel": parsed.hotel_name, "check_in": parsed.check_in.isoformat()},
        )
        return IngestionResult(status="not_refundable")

    job = _build_job(user.id, parsed)
    _resolve_hotel(job, parsed)
    db.add(job)

    # Decide the starting state from what's still missing.
    if job.hotel_id is None:
        job.status = JobStatus.pending_hotel.value
        db.commit()
        db.refresh(job)
        klaviyo.emit_event(
            klaviyo.EVENT_HOTEL_RESOLUTION_NEEDED,
            user.email,
            {"hotel": job.hotel_name_raw, "candidates": job.hotel_candidates},
        )
        return IngestionResult(status="pending_hotel", job_id=job.id)

    if job.cancellation_deadline is None:
        job.status = JobStatus.pending_info.value
        db.commit()
        db.refresh(job)
        klaviyo.emit_event(
            klaviyo.EVENT_NEEDS_DEADLINE, user.email, {"hotel": job.hotel_name_raw}
        )
        return IngestionResult(status="pending_info", job_id=job.id)

    # Everything present → monitor immediately.
    job.status = JobStatus.active.value
    job.next_check_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    klaviyo.emit_event(
        klaviyo.EVENT_BOOKING_MONITORED, user.email, klaviyo.booking_monitored_properties(job)
    )
    return IngestionResult(status="monitoring", job_id=job.id)


def _build_job(user_id: int, parsed: ParsedBooking) -> models.MonitoringJob:
    return models.MonitoringJob(
        user_id=user_id,
        hotel_name_raw=parsed.hotel_name,
        city=parsed.city,
        country=parsed.country,
        check_in=parsed.check_in,
        check_out=parsed.check_out,
        nights=max((parsed.check_out - parsed.check_in).days, 0),
        room_type_raw=parsed.room_type_raw,
        board_type=parsed.board_type.value if parsed.board_type else None,
        adults=parsed.adults,
        children=parsed.children,
        original_price=Decimal(str(parsed.total_price)),
        currency=parsed.currency.upper(),
        original_ota=parsed.ota,
        refundable=True,  # guarded above
        cancellation_deadline=parsed.cancellation_deadline,
    )


def _resolve_hotel(job: models.MonitoringJob, parsed: ParsedBooking) -> None:
    """High-confidence single match → store hotel_id. Otherwise stash the
    candidates so we can ask the user once, and never ask again (§6b)."""
    try:
        matches = get_price_source().resolve_hotel(
            parsed.hotel_name, parsed.city, country=parsed.country
        )
    except Exception:  # pragma: no cover - resolution failures are non-fatal
        logger.exception("Hotel resolution failed for %s", parsed.hotel_name)
        return

    if len(matches) == 1 and matches[0].confidence >= 0.9:
        job.hotel_id = matches[0].hotel_id
    elif matches:
        job.hotel_candidates = json.dumps(
            [
                {"hotel_id": m.hotel_id, "name": m.name, "city": m.city, "confidence": m.confidence}
                for m in matches
            ]
        )
