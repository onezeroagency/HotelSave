"""Job activation: the transition from a pending (parsed-but-incomplete) job to
an actively-monitored one. Shared by ingestion (§6) and the CRUD PATCH path so a
user supplying a missing deadline or picking their hotel flips the job to active
the same way, wherever the update came from."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..enums import JobStatus
from ..models import MonitoringJob
from . import klaviyo

_PENDING = {s.value for s in JobStatus.pending()}


def is_ready_to_monitor(job: MonitoringJob) -> bool:
    """A job can be monitored once it's refundable, has a deadline to clock
    against (§8), and is resolved to a hotel_id (§6b)."""
    return bool(job.refundable) and job.cancellation_deadline is not None and job.hotel_id is not None


def activate(db: Session, job: MonitoringJob, email: str) -> None:
    job.status = JobStatus.active.value
    job.next_check_at = datetime.now(timezone.utc)  # eligible immediately
    db.commit()
    db.refresh(job)
    klaviyo.emit_event(
        klaviyo.EVENT_BOOKING_MONITORED, email, klaviyo.booking_monitored_properties(job)
    )


def activate_if_ready(db: Session, job: MonitoringJob, email: str) -> bool:
    """Activate a pending job iff it now has everything it needs. Returns whether
    it activated."""
    if job.status in _PENDING and is_ready_to_monitor(job):
        activate(db, job, email)
        return True
    return False
