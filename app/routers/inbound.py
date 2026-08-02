"""Inbound-email webhooks (§5). Postmark POSTs a parsed email here; we normalize
it and hand it to ingestion. Always returns 200 so the provider doesn't retry on
business-logic outcomes (unknown sender, non-refundable, etc.)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..services.email_inbound import parse_postmark
from ..services.ingestion import process_inbound_email

router = APIRouter(prefix="/inbound", tags=["inbound-email"])


def _check_secret(token: str | None) -> None:
    """Optional shared-secret guard on the webhook URL (?token=...). Inbound
    providers can't do bearer auth, so a URL secret is the usual protection."""
    if settings.inbound_webhook_secret and token != settings.inbound_webhook_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.post("/postmark", status_code=status.HTTP_200_OK)
def postmark_inbound(
    payload: dict,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    _check_secret(token)
    inbound = parse_postmark(payload)
    if not inbound.from_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing sender address"
        )
    result = process_inbound_email(db, inbound)
    return {"status": result.status, "job_id": result.job_id}
