"""Tests for the Google Hotels (SerpApi) price source (§9). No network — the
HTTP client is mocked. Response shapes mirror SerpApi's published docs
(google-hotels-api / google-hotels-property-details, read 2026-11-24); live
field names are confirmed separately by scripts/validate_google_hotels.py."""

from datetime import date
from decimal import Decimal

import pytest

from app.config import settings
from app.services.price_source.google_hotels import GoogleHotelsPriceSource


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return _FakeResponse(self._payload)


@pytest.fixture()
def source(monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", "test_key")
    return GoogleHotelsPriceSource()


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "serpapi_key", None)
    with pytest.raises(RuntimeError):
        GoogleHotelsPriceSource()


# -- resolve (§6b) -------------------------------------------------------------


def test_strong_name_match_auto_resolves_to_the_top_hit(source):
    """Ranked search ≠ filtered lookup: nearby alternatives must not push every
    job into the ask-the-user flow. A top hit whose name agrees with the
    booking comes back ALONE at auto-resolve confidence."""
    source._client = _FakeClient({
        "properties": [
            {"name": "Grand Poet Hotel and SPA by Semarah", "property_token": "TOK1",
             "gps_coordinates": {"latitude": 56.95, "longitude": 24.1}},
            {"name": "Some Other Riga Hotel", "property_token": "TOK2"},
        ]
    })
    matches = source.resolve_hotel(
        "Grand Poet Hotel and SPA by Semarah", "Riga", country="LV",
        check_in=date(2026, 12, 10), check_out=date(2026, 12, 12),
    )
    assert len(matches) == 1
    assert matches[0].hotel_id == "TOK1"
    assert matches[0].confidence >= 0.9

    _, params = source._client.calls[0]
    assert params["check_in_date"] == "2026-12-10"
    assert params["gl"] == "lv"
    assert "Grand Poet" in params["q"] and "Riga" in params["q"]


def test_weak_match_stays_ambiguous(source):
    source._client = _FakeClient({
        "properties": [
            {"name": "Completely Different Resort", "property_token": "TOK1"},
            {"name": "Another Place", "property_token": "TOK2"},
        ]
    })
    matches = source.resolve_hotel("Grand Poet Hotel", "Riga")
    assert len(matches) == 2
    assert all(m.confidence < 0.9 for m in matches)  # → pending_hotel (§6b)


def test_resolve_without_dates_synthesizes_a_valid_window(source):
    """Google Hotels refuses a search without dates; callers without a booking
    still get a token."""
    source._client = _FakeClient({"properties": []})
    source.resolve_hotel("Hotel X")
    _, params = source._client.calls[0]
    assert params["check_in_date"] < params["check_out_date"]


def test_serpapi_error_payload_returns_no_matches(source):
    source._client = _FakeClient({"error": "Google Hotels hasn't returned any results."})
    assert source.resolve_hotel("Hotel X", "Riga") == []


# -- check (§7/§8) -------------------------------------------------------------

_CHECK_ARGS = ("TOK1", date(2026, 12, 10), date(2026, 12, 12), 2, 0)


def test_ota_level_prices_map_with_explicit_refundability(source):
    source._client = _FakeClient({
        "prices": [
            {
                "source": "Booking.com",
                "num_guests": 2,
                "total_rate": {"extracted_before_taxes_fees": 300,
                               "extracted_after_taxes_fees": 339},
                "free_cancellation": True,
                "free_cancellation_until_date": "Dec 8",
            },
            {
                "source": "Expedia.com",
                "num_guests": 2,
                "total_rate": {"extracted_lowest": 349},
                # no free_cancellation flag → NOT asserted refundable
            },
        ]
    })
    got = source.check(*_CHECK_ARGS)
    assert len(got) == 2

    booking = next(c for c in got if c.ota == "Booking.com")
    assert booking.total_price == Decimal("339")  # after-tax, never the pre-tax 300
    assert booking.refundable is True
    assert booking.board_type is None  # unknown, not asserted
    assert booking.deep_link is None  # trigger-grade: never opens the alert gate
    # Year-less "Dec 8" is deliberately NOT parsed into a deadline.
    assert booking.free_cancellation_until is None

    expedia = next(c for c in got if c.ota == "Expedia.com")
    assert expedia.refundable is False


def test_pre_tax_only_entries_are_skipped_not_underpriced(source):
    """Comparing a pre-tax figure against the user's tax-inclusive original is
    the phantom-drop bug from the other direction."""
    source._client = _FakeClient({
        "prices": [
            {"source": "SomeOTA",
             "total_rate": {"extracted_before_taxes_fees": 300,
                            "extracted_display_price": 300}},
        ]
    })
    assert source.check(*_CHECK_ARGS) == []


def test_room_level_entries_carry_name_and_breakfast_board(source):
    source._client = _FakeClient({
        "featured_prices": [
            {
                "source": "Hilton",
                "rooms": [
                    {
                        "name": "KING GUEST RM GARDEN VIEW",
                        "num_guests": 2,
                        "total_rate": {"extracted_after_taxes_fees": 384},
                        "rates": [{"breakfast_included": True,
                                   "inclusions": ["1 king bed", "breakfast"]}],
                    },
                    {
                        "name": "Standard Twin",
                        "num_guests": 2,
                        "total_rate": {"extracted_lowest": 350},
                        "rates": [{"inclusions": ["1 twin bed"]}],
                    },
                ],
            }
        ]
    })
    got = source.check(*_CHECK_ARGS)
    king = next(c for c in got if c.room_name == "KING GUEST RM GARDEN VIEW")
    assert king.board_type == "BB"
    assert king.total_price == Decimal("384")
    twin = next(c for c in got if c.room_name == "Standard Twin")
    assert twin.board_type is None  # no breakfast signal ≠ room only


def test_check_passes_stay_and_occupancy(source):
    source._client = _FakeClient({"prices": []})
    source.check(*_CHECK_ARGS)
    _, params = source._client.calls[0]
    assert params["property_token"] == "TOK1"
    assert params["check_in_date"] == "2026-12-10"
    assert params["adults"] == 2
    assert params["currency"] == "EUR"
