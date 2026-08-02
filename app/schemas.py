"""Pydantic request/response schemas."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .enums import BoardType, JobStatus, PlanStatus

# --- auth ---


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    plan_status: PlanStatus
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- monitoring jobs ---


class MonitoringJobCreate(BaseModel):
    hotel_name_raw: str
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lng: float | None = None

    check_in: date
    check_out: date

    room_type_raw: str | None = None
    board_type: BoardType | None = None
    adults: int | None = None
    children: int | None = None

    original_price: Decimal
    currency: str = Field(min_length=3, max_length=3)
    original_ota: str | None = None

    refundable: bool = False
    cancellation_deadline: datetime | None = None
    cancellation_terms_raw: str | None = None


class MonitoringJobUpdate(BaseModel):
    """Partial update — all fields optional."""

    hotel_id: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lng: float | None = None
    room_type_raw: str | None = None
    board_type: BoardType | None = None
    adults: int | None = None
    children: int | None = None
    refundable: bool | None = None
    cancellation_deadline: datetime | None = None
    cancellation_terms_raw: str | None = None
    status: JobStatus | None = None


class MonitoringJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: JobStatus

    hotel_name_raw: str
    hotel_id: str | None
    city: str | None
    country: str | None

    check_in: date
    check_out: date
    nights: int

    room_type_raw: str | None
    board_type: BoardType | None
    adults: int | None
    children: int | None

    original_price: Decimal
    currency: str
    original_ota: str | None

    refundable: bool
    cancellation_deadline: datetime | None

    created_at: datetime
    last_checked_at: datetime | None
    next_check_at: datetime | None
    check_count: int
    current_best_price: Decimal | None
    current_best_ota: str | None
    current_best_url: str | None
    lowest_seen_price: Decimal | None
