"""Room matching — correctness *is* ethics (§7).

A candidate rate counts as a drop only if ALL hold:
  1. same hotel_id + same check_in/check_out  (guaranteed by how we query)
  2. matches on {adults, children, board_type, room class}
  3. candidate is itself refundable = true, and its free-cancellation window is
     no shorter than the one the user already holds
  4. candidate total beats original_price by more than the floor: max(€10, 3%)
"""

import re
from datetime import datetime, timezone
from decimal import Decimal

from ..config import settings
from ..models import MonitoringJob
from .price_source.base import RateCandidate


def drop_floor(original_price: Decimal) -> Decimal:
    """max(abs floor, pct of original) — kills currency noise and €2 spam (§7 r4)."""
    return max(
        Decimal(str(settings.drop_floor_abs)),
        original_price * Decimal(str(settings.drop_floor_pct)),
    )


# Room-class vocabulary, cheapest tier first. Providers name the same room a
# dozen ways ("Standard Double Room", "Double Room - Standard"), so comparing the
# raw strings would reject genuine matches; comparing the *class* word is the
# part that actually decides whether it's the same product.
_ROOM_CLASSES = (
    "presidential",
    "penthouse",
    "junior suite",  # before "suite" — "junior suite" contains it
    "suite",
    "executive",
    "premium",
    "deluxe",
    "luxury",
    "superior",
    "comfort",
    "classic",
    "standard",
    "economy",
    "budget",
)


def _as_utc(moment: datetime) -> datetime:
    """Naive stamps (providers often omit the offset) are read as UTC so the two
    deadlines can be compared at all — mixing naive and aware raises."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def room_class(name: str | None) -> str | None:
    """The tier word in a room name, or None if it doesn't state one."""
    if not name:
        return None
    text = re.sub(r"\s+", " ", name).strip().lower()
    for cls in _ROOM_CLASSES:
        if cls in text:
            return cls
    return None


def _matches_room(candidate: RateCandidate, job: MonitoringJob) -> bool:
    if not candidate.refundable:  # rule 3 — never compare against a non-refundable trap
        return False
    # Rule 3b: "refundable" is a boolean, but the free-cancellation *window* is
    # the product. A rate that stops being cancellable sooner than the booking
    # the user already holds is a downgrade, however cheap — swapping into it
    # silently shortens the time they have to change their mind.
    if (
        job.cancellation_deadline is not None
        and candidate.free_cancellation_until is not None
        and _as_utc(candidate.free_cancellation_until) < _as_utc(job.cancellation_deadline)
    ):
        return False
    if job.board_type is not None and candidate.board_type != job.board_type:
        return False  # rule 2
    # Rule 2, room class: a cheaper *different* room is not a cheaper booking.
    # Only decidable when both sides name a class — an unnamed room is unknown,
    # not a match, so it can't be used to manufacture one either way.
    booked_class = room_class(job.room_type_raw)
    found_class = room_class(candidate.room_name)
    if booked_class and found_class and booked_class != found_class:
        return False
    if job.adults is not None and candidate.adults is not None and candidate.adults != job.adults:
        return False
    if (
        job.children is not None
        and candidate.children is not None
        and candidate.children != job.children
    ):
        return False
    return True


def best_like_for_like(
    candidates: list[RateCandidate], job: MonitoringJob
) -> RateCandidate | None:
    """Lowest-priced candidate that satisfies rules 1–3. The floor (rule 4) is a
    drop-detection concern, applied by the scheduler — not a matching concern."""
    eligible = [c for c in candidates if _matches_room(c, job)]
    if not eligible:
        return None
    return min(eligible, key=lambda c: c.total_price)


def is_actionable_drop(best: RateCandidate | None, job: MonitoringJob) -> bool:
    """True if `best` beats the original by more than the floor (rule 4)."""
    if best is None:
        return False
    return best.total_price < job.original_price - drop_floor(job.original_price)
