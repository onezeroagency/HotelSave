"""Plan gating (§11): the free tier watches 1 active booking at a time, pro is
unlimited. "Active" means any booking we're still watching or still waiting on
the user for — finished and cancelled jobs never count against the cap."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..enums import JobStatus

FREE_ACTIVE_JOB_LIMIT = 1
UPGRADE_URL = "https://www.myroomwatch.com/pricing"
LIMIT_MESSAGE = (
    "Free plan watches 1 active booking at a time — upgrade at "
    f"{UPGRADE_URL} for unlimited bookings."
)

# Watched + pending states: everything that is, or will become, a live watch.
_COUNTED_STATUSES = tuple(
    s.value for s in (*JobStatus.monitored(), *JobStatus.pending())
)


def active_job_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(models.MonitoringJob)
            .where(
                models.MonitoringJob.user_id == user_id,
                models.MonitoringJob.status.in_(_COUNTED_STATUSES),
            )
        )
        or 0
    )


def at_free_limit(db: Session, user: models.User) -> bool:
    """True when a free-tier user already has their 1 active booking."""
    if user.is_pro:
        return False
    return active_job_count(db, user.id) >= FREE_ACTIVE_JOB_LIMIT
