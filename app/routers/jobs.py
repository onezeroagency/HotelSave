"""MonitoringJob CRUD, scoped to the authenticated user."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user
from ..enums import JobStatus
from ..services import klaviyo, lifecycle
from ..services.price_source import get_price_source

logger = logging.getLogger("hotelsave.jobs")

router = APIRouter(prefix="/jobs", tags=["monitoring-jobs"])


def _get_owned_job(job_id: int, user: models.User, db: Session) -> models.MonitoringJob:
    job = db.get(models.MonitoringJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("", response_model=schemas.MonitoringJobRead, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: schemas.MonitoringJobCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> models.MonitoringJob:
    if payload.check_out <= payload.check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="check_out must be after check_in",
        )

    job = models.MonitoringJob(
        user_id=current_user.id,
        status=JobStatus.active.value,
        hotel_name_raw=payload.hotel_name_raw,
        city=payload.city,
        country=payload.country,
        lat=payload.lat,
        lng=payload.lng,
        check_in=payload.check_in,
        check_out=payload.check_out,
        nights=(payload.check_out - payload.check_in).days,
        room_type_raw=payload.room_type_raw,
        board_type=payload.board_type.value if payload.board_type else None,
        adults=payload.adults,
        children=payload.children,
        original_price=payload.original_price,
        currency=payload.currency.upper(),
        original_ota=payload.original_ota,
        refundable=payload.refundable,
        cancellation_deadline=payload.cancellation_deadline,
        cancellation_terms_raw=payload.cancellation_terms_raw,
        next_check_at=datetime.now(timezone.utc),  # eligible immediately
    )

    # Best-effort hotel-ID resolution (§6b). High-confidence single match → store it;
    # anything ambiguous is left for the resolve step to ask the user later.
    try:
        matches = get_price_source().resolve_hotel(
            payload.hotel_name_raw, payload.city, payload.lat, payload.lng
        )
        if len(matches) == 1 and matches[0].confidence >= 0.9:
            job.hotel_id = matches[0].hotel_id
    except Exception:  # pragma: no cover - resolution is non-fatal at create time
        # Non-fatal, but never silent: the scheduler retries (§6b), and this log
        # is the only breadcrumb when e.g. the price-source credentials are bad.
        logger.exception(
            "Hotel resolution failed at create for %r — job starts without hotel_id",
            payload.hotel_name_raw,
        )

    db.add(job)
    db.commit()
    db.refresh(job)

    klaviyo.emit_event(
        klaviyo.EVENT_BOOKING_MONITORED,
        current_user.email,
        klaviyo.booking_monitored_properties(job),
    )
    return job


@router.get("", response_model=list[schemas.MonitoringJobRead])
def list_jobs(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[models.MonitoringJob]:
    stmt = (
        select(models.MonitoringJob)
        .where(models.MonitoringJob.user_id == current_user.id)
        .order_by(models.MonitoringJob.created_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.get("/{job_id}", response_model=schemas.MonitoringJobRead)
def get_job(
    job_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> models.MonitoringJob:
    return _get_owned_job(job_id, current_user, db)


@router.patch("/{job_id}", response_model=schemas.MonitoringJobRead)
def update_job(
    job_id: int,
    payload: schemas.MonitoringJobUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> models.MonitoringJob:
    job = _get_owned_job(job_id, current_user, db)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field in ("board_type", "status") and value is not None:
            value = value.value  # enum → stored string
        setattr(job, field, value)
    db.commit()
    db.refresh(job)
    # If the user just supplied what a pending job was waiting on (a deadline, or
    # picked their hotel via hotel_id), flip it to active and start monitoring (§6).
    lifecycle.activate_if_ready(db, job, current_user.email)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(
    job_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Soft-cancel: stop monitoring (§4 `cancelled`) rather than hard-delete, so
    PriceCheck history survives for analytics."""
    job = _get_owned_job(job_id, current_user, db)
    job.status = JobStatus.cancelled.value
    job.next_check_at = None
    db.commit()
