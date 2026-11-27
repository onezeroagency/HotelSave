"""Shared enums — the state machine (§4) and small controlled vocabularies."""

import enum


class PlanStatus(str, enum.Enum):
    """User.plan_status (§3, §11): the two live tiers are `free` and `pro`,
    synced from Stripe by the billing webhook. The other values are legacy
    rows from before the tiers existed and read as: trialing ≈ free,
    active ≈ pro; past_due/canceled pause monitoring entirely."""

    free = "free"
    pro = "pro"

    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"


class JobStatus(str, enum.Enum):
    """MonitoringJob lifecycle (§4), plus two pre-monitoring holding states from
    the parse/resolve step (§6) where a job exists but isn't yet actionable."""

    # Pre-monitoring: created from an email but waiting on the user.
    pending_hotel = "pending_hotel"  # ambiguous hotel match — awaiting a pick (§6b)
    pending_info = "pending_info"  # refundable but cancellation_deadline unknown (§6a)

    active = "active"
    drop_found = "drop_found"
    deadline_soon = "deadline_soon"
    expired = "expired"
    cancelled = "cancelled"

    # Statuses the scheduler actively polls.
    @classmethod
    def monitored(cls) -> tuple["JobStatus", ...]:
        return (cls.active, cls.drop_found, cls.deadline_soon)

    # Pre-monitoring holding states, resolved into `active` once complete.
    @classmethod
    def pending(cls) -> tuple["JobStatus", ...]:
        return (cls.pending_hotel, cls.pending_info)


class BoardType(str, enum.Enum):
    """Meal plan — part of the like-for-like tuple (§7)."""

    RO = "RO"  # room only
    BB = "BB"  # bed & breakfast
    HB = "HB"  # half board
    FB = "FB"  # full board
