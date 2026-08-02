"""Tests for the Hotellook price source (§9). No network — the HTTP client is
mocked. The signature is pinned to Travelpayouts' documented example so the
sort/join/prefix logic is verified exactly, not guessed."""

import hashlib
from datetime import date
from decimal import Decimal

import pytest

from app.config import settings
from app.services.price_source import hotellook
from app.services.price_source.hotellook import HotellookPriceSource, _signature


def test_signature_matches_documented_vector():
    # From Travelpayouts docs — the exact string that must be md5'd:
    documented = (
        "WriteHereYourToken:WriteHereYourMarker:"
        "2:2020-12-10:2020-12-13:10:1:USD:77.111.247.75:HKT:ru:0"
    )
    expected = hashlib.md5(documented.encode()).hexdigest()

    params = {
        "adultsCount": 2,
        "checkIn": "2020-12-10",
        "checkOut": "2020-12-13",
        "childAge1": 10,
        "childrenCount": 1,
        "currency": "USD",
        "customerIP": "77.111.247.75",
        "iata": "HKT",
        "lang": "ru",
        "waitForResult": 0,
    }
    assert _signature("WriteHereYourToken", "WriteHereYourMarker", params) == expected


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Dispatches by URL so one client can back the whole search flow."""

    def __init__(self, lookup=None, start=None, result=None):
        self._lookup = lookup or {"results": {"hotels": []}}
        self._start = start or {"searchId": 42}
        self._result = result or {"result": []}
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if "lookup" in url:
            return _FakeResponse(self._lookup)
        if "start" in url:
            return _FakeResponse(self._start)
        return _FakeResponse(self._result)


@pytest.fixture()
def source(monkeypatch):
    monkeypatch.setattr(settings, "travelpayouts_token", "TOKEN")
    monkeypatch.setattr(settings, "travelpayouts_marker", "759266")
    monkeypatch.setattr(settings, "travelpayouts_customer_ip", None)
    return HotellookPriceSource()


def test_resolve_single_hotel_is_high_confidence(source):
    source._client = _FakeClient(
        lookup={"results": {"hotels": [{"id": 123, "label": "Grand Vilnius", "locationName": "Vilnius"}]}}
    )
    matches = source.resolve_hotel("Grand Vilnius", "Vilnius")
    assert len(matches) == 1
    assert matches[0].hotel_id == "123"
    assert matches[0].confidence >= 0.9


def test_resolve_multiple_hotels_is_ambiguous(source):
    source._client = _FakeClient(
        lookup={"results": {"hotels": [{"id": 1, "label": "A"}, {"id": 2, "label": "B"}]}}
    )
    matches = source.resolve_hotel("Grand", None)
    assert len(matches) == 2
    assert all(m.confidence < 0.9 for m in matches)  # → pending_hotel


def test_check_maps_rooms_to_candidates(source):
    source._client = _FakeClient(
        start={"searchId": 42},
        result={
            "result": [
                {
                    "id": 123,
                    "rooms": [
                        {
                            "agencyName": "Booking.com",
                            "total": 380,
                            "fullBookingURL": "https://tp.media/book?marker=759266",
                            "options": {"refundable": True, "breakfast": True},
                        },
                        {
                            "agencyName": "Expedia",
                            "total": 400,
                            "options": {"refundable": False, "breakfast": False},
                        },
                    ],
                }
            ]
        },
    )
    candidates = source.check("123", date(2026, 9, 10), date(2026, 9, 13), 2, 0)
    assert len(candidates) == 2

    refundable = next(c for c in candidates if c.refundable)
    assert refundable.ota == "Booking.com"
    assert refundable.total_price == Decimal("380")
    assert refundable.board_type == "BB"  # breakfast → BB
    assert refundable.deep_link.endswith("marker=759266")


def test_check_ignores_other_hotels(source):
    source._client = _FakeClient(
        result={"result": [{"id": 999, "rooms": [{"agencyName": "X", "total": 100, "options": {}}]}]}
    )
    assert source.check("123", date(2026, 9, 10), date(2026, 9, 13), 2, 0) == []


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.setattr(settings, "travelpayouts_token", None)
    monkeypatch.setattr(settings, "travelpayouts_marker", None)
    with pytest.raises(RuntimeError):
        HotellookPriceSource()
