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


def _rate(price, refundable=True, board="BB", adults=2, children=0, ota="Booking.com",
          room_name=None):
    return RateCandidate(
        ota=ota,
        total_price=Decimal(str(price)),
        currency="EUR",
        board_type=board,
        adults=adults,
        children=children,
        refundable=refundable,
        deep_link="https://example.test/rebook",
        room_name=room_name,
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


# --- rule 2, room class -------------------------------------------------------
# Live 2026-08-29: a Riga stay booked at the cheapest refundable rate its OTA
# sold was reported as ~22% cheaper. Room class wasn't the culprit that time
# (the booking was already the entry-level room), but nothing was comparing it,
# so a cheaper *different* room would have matched just as silently.


def test_cheaper_different_room_class_is_not_a_match():
    job = _job(room_type_raw="Deluxe Double Room")
    rates = [_rate("300.00", room_name="Standard Double Room")]
    assert best_like_for_like(rates, job) is None


def test_same_room_class_matches_despite_different_wording():
    job = _job(room_type_raw="Standard Double Room")
    rates = [_rate("300.00", room_name="Double Room - Standard, 1 queen bed")]
    assert best_like_for_like(rates, job) is not None


def test_unnamed_room_does_not_block_a_match():
    """Providers that don't name rooms must not lose every drop — unknown is
    unknown, and the rebook-link gate is the real safety net there."""
    job = _job(room_type_raw="Standard Double Room")
    assert best_like_for_like([_rate("300.00", room_name=None)], job) is not None
    assert best_like_for_like([_rate("300.00", room_name="Room")], job) is not None


def test_junior_suite_is_not_a_suite():
    job = _job(room_type_raw="Junior Suite")
    assert best_like_for_like([_rate("300.00", room_name="Suite")], job) is None
    assert best_like_for_like([_rate("300.00", room_name="Junior Suite")], job) is not None


def test_room_class_picks_cheapest_within_the_same_class():
    job = _job(room_type_raw="Superior King")
    rates = [
        _rate("390.00", room_name="Superior King Room"),
        _rate("310.00", room_name="Superior King Room"),
        _rate("200.00", room_name="Standard Twin"),  # cheaper, wrong class
    ]
    best = best_like_for_like(rates, job)
    assert best is not None
    assert best.total_price == Decimal("310.00")
