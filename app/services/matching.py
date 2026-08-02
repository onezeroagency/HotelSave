"""Room matching — correctness *is* ethics (§7).

A candidate rate counts as a drop only if ALL hold:
  1. same hotel_id + same check_in/check_out  (guaranteed by how we query)
  2. matches on {adults, children, board_type}
  3. candidate is itself refundable = true
  4. candidate total beats original_price by more than the floor: max(€10, 3%)
"""

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


def _matches_room(candidate: RateCandidate, job: MonitoringJob) -> bool:
    if not candidate.refundable:  # rule 3 — never compare against a non-refundable trap
        return False
    if job.board_type is not None and candidate.board_type != job.board_type:
        return False  # rule 2
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
