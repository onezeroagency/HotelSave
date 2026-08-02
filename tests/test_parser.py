"""Tests for the mock booking parser (§6a)."""

from datetime import date

from app.enums import BoardType
from app.services.parser.mock import MockParser

EMAIL = """
Hotel: Grand Vilnius
City: Vilnius
Country: LT
Check-in: 2026-09-10
Check-out: 2026-09-13
Room: Superior Double, City View
Board: BB
Adults: 2
Children: 0
Total: 420
Currency: eur
OTA: Booking.com
Refundable: Yes
Cancellation: 2026-09-07T23:59
"""


def test_parses_full_email():
    parsed = MockParser().parse(EMAIL)
    assert parsed.hotel_name == "Grand Vilnius"
    assert parsed.city == "Vilnius"
    assert parsed.check_in == date(2026, 9, 10)
    assert parsed.check_out == date(2026, 9, 13)
    assert parsed.board_type == BoardType.BB
    assert parsed.adults == 2
    assert parsed.total_price == 420.0
    assert parsed.currency == "EUR"  # upper-cased
    assert parsed.refundable is True
    assert parsed.cancellation_deadline is not None


def test_defaults_when_fields_absent():
    parsed = MockParser().parse("just some text with no fields")
    assert parsed.hotel_name == "Test Hotel"
    assert parsed.currency == "EUR"
    assert parsed.total_price == 300.0
    assert parsed.refundable is None  # not stated → not guessed
    assert parsed.check_out > parsed.check_in
