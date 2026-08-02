"""Unit tests for the like-for-like matching rules (§7)."""

from decimal import Decimal

from app.models import MonitoringJob
from app.services.matching import best_like_for_like, drop_floor, is_actionable_drop
from app.services.price_source.base import RateCandidate


def _job(**kw):
    defaults = dict(
        original_price=Decimal("420.00"),
        board_type="BB",
        adults=2,
        children=0,
    )
    defaults.update(kw)
    return MonitoringJob(**defaults)


def _rate(price, refundable=True, board="BB", adults=2, children=0, ota="Booking.com"):
    return RateCandidate(
        ota=ota,
        total_price=Decimal(str(price)),
        currency="EUR",
        board_type=board,
        adults=adults,
        children=children,
        refundable=refundable,
        deep_link="https://example.test/rebook",
    )


def test_drop_floor_is_max_of_abs_and_pct():
    assert drop_floor(Decimal("100")) == Decimal("10")  # abs floor wins
    assert drop_floor(Decimal("1000")) == Decimal("30")  # pct (3%) wins


def test_non_refundable_candidate_never_matches():
    job = _job()
    assert best_like_for_like([_rate(300, refundable=False)], job) is None


def test_board_type_mismatch_excluded():
    job = _job()
    assert best_like_for_like([_rate(300, board="HB")], job) is None


def test_picks_lowest_eligible():
    job = _job()
    best = best_like_for_like([_rate(390), _rate(360), _rate(400)], job)
    assert best.total_price == Decimal("360")


def test_is_actionable_drop_respects_floor():
    job = _job(original_price=Decimal("420.00"))
    # 415 is only €5 under → below the €10 floor → not actionable.
    assert is_actionable_drop(_rate(415), job) is False
    # 400 is €20 under → actionable.
    assert is_actionable_drop(_rate(400), job) is True
