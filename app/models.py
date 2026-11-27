"""ORM models — the whole system is one object (MonitoringJob) with a state
machine around it (§3, §4). Everything else is plumbing."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .enums import JobStatus, PlanStatus

# Money: fixed-precision decimals, never floats.
Money = Numeric(10, 2)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # NULL for passwordless (magic-link) accounts. Password login is kept for
    # accounts that have one; both paths issue the same JWT.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # GDPR consent record: the flag AND when it was given. Only ever set from
    # an explicit, unchecked-by-default checkbox; never inferred.
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    marketing_opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Rotating nonce that makes magic-link tokens single-use: a token is only
    # valid while it carries the CURRENT nonce, and consuming or reissuing a
    # link rotates it.
    magic_nonce: Mapped[str | None] = mapped_column(String(64))

    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    # Meta ad-attribution cookies (_fbp/_fbc), captured when the browser
    # reports checkout intent, so the server-side Purchase event can be
    # matched back to the ad click.
    meta_fbp: Mapped[str | None] = mapped_column(String(128))
    meta_fbc: Mapped[str | None] = mapped_column(String(512))
    plan_status: Mapped[str] = mapped_column(
        String(20), default=PlanStatus.free.value, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    jobs: Mapped[list["MonitoringJob"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def can_monitor(self) -> bool:
        """free/pro (and their legacy spellings) monitor; past_due/canceled are
        paused (§11). How MANY bookings may be watched is services.plan's job."""
        return self.plan_status in (
            PlanStatus.free.value,
            PlanStatus.pro.value,
            PlanStatus.trialing.value,
            PlanStatus.active.value,
        )

    @property
    def is_pro(self) -> bool:
        """Paying subscriber (unlimited bookings). `active` is the legacy
        spelling of pro from before the free/pro tiers existed."""
        return self.plan_status in (PlanStatus.pro.value, PlanStatus.active.value)


class MonitoringJob(Base):
    __tablename__ = "monitoring_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=JobStatus.active.value, nullable=False, index=True
    )

    # --- hotel identity ---
    hotel_name_raw: Mapped[str] = mapped_column(String(500), nullable=False)
    hotel_id: Mapped[str | None] = mapped_column(String(100), index=True)
    hotel_candidates: Mapped[str | None] = mapped_column(Text)  # JSON pick-list when ambiguous (§6b)
    city: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(100))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    # --- the stay ---
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    nights: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- the room (this tuple defines "same room", §7) ---
    room_type_raw: Mapped[str | None] = mapped_column(String(500))
    board_type: Mapped[str | None] = mapped_column(String(4))
    adults: Mapped[int | None] = mapped_column(Integer)
    children: Mapped[int | None] = mapped_column(Integer)

    # --- the rate to beat ---
    original_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    original_ota: Mapped[str | None] = mapped_column(String(100))

    # --- policy: the actionability clock ---
    refundable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancellation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_terms_raw: Mapped[str | None] = mapped_column(Text)

    # --- monitoring state ---
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    check_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_best_price: Mapped[Decimal | None] = mapped_column(Money)
    current_best_ota: Mapped[str | None] = mapped_column(String(100))
    current_best_url: Mapped[str | None] = mapped_column(Text)  # affiliate deep-link
    lowest_seen_price: Mapped[Decimal | None] = mapped_column(Money)

    # --- alerting idempotency guards (§8) ---
    drop_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lowest_alerted_price: Mapped[Decimal | None] = mapped_column(Money)
    deadline_alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="jobs")
    price_checks: Mapped[list["PriceCheck"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class PriceCheck(Base):
    """History table (§3) — powers trend/retention emails and debugging bad matches.
    Kept from day one even though it isn't surfaced yet."""

    __tablename__ = "price_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("monitoring_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    best_matching_price: Mapped[Decimal | None] = mapped_column(Money)
    best_ota: Mapped[str | None] = mapped_column(String(100))
    raw_response_ref: Mapped[str | None] = mapped_column(Text)

    job: Mapped["MonitoringJob"] = relationship(back_populates="price_checks")
