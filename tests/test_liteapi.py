"""Tests for the LiteAPI price source (§9). No network — the HTTP client is
mocked. The response shapes mirror the official liteapi-travel/nodejs-sdk
(data[].roomTypes[].rates[]); live field names are confirmed separately by
scripts/validate_liteapi.py."""

from datetime import date
from decimal import Decimal

import pytest

from app.config import settings
from app.services.price_source.liteapi import LiteAPIPriceSource, _board_type


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, lookup=None, rates=None):
        self._lookup = lookup or {"data": []}
        self._rates = rates or {"data": []}
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return _FakeResponse(self._lookup)

    def post(self, url, json=None):
        self.calls.append(("POST", url, json))
        return _FakeResponse(self._rates)


@pytest.fixture()
def source(monkeypatch):
    monkeypatch.setattr(settings, "liteapi_key", "sand_test")
    monkeypatch.setattr(settings, "liteapi_country_code", "LV")
    monkeypatch.setattr(settings, "liteapi_guest_nationality", "LV")
    src = LiteAPIPriceSource()
    return src


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "liteapi_key", None)
    with pytest.raises(RuntimeError):
        LiteAPIPriceSource()


def test_resolve_single_hotel_is_high_confidence(source):
    source._client = _FakeClient(
        lookup={"data": [{"id": "lp12345", "name": "Amrita Hotel", "city": "Liepaja",
                          "country": "LV", "latitude": 56.5, "longitude": 21.0}]}
    )
    matches = source.resolve_hotel("Amrita Hotel", "Liepaja")
    assert len(matches) == 1
    assert matches[0].hotel_id == "lp12345"
    assert matches[0].confidence >= 0.9
    # country filter from settings must be passed through
    _, _, params = source._client.calls[0]
    assert params["countryCode"] == "LV"
    assert params["hotelName"] == "Amrita Hotel"


def test_resolve_multiple_hotels_is_ambiguous(source):
    source._client = _FakeClient(
        lookup={"data": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}
    )
    matches = source.resolve_hotel("Hotel", None)
    assert len(matches) == 2
    assert all(m.confidence < 0.9 for m in matches)  # → pending_hotel (§6b)


def test_check_maps_rates_to_candidates(source):
    source._client = _FakeClient(
        rates={"data": [{
            "hotelId": "lp12345",
            "roomTypes": [{
                "offerId": "of_1",
                "rates": [
                    {
                        "name": "Standard Double",
                        "boardType": "BI",
                        "boardName": "Breakfast included",
                        "retailRate": {"total": [{"amount": 212.0, "currency": "EUR"}]},
                        "cancellationPolicies": {"refundableTag": "RFN"},
                    },
                    {
                        "name": "Standard Double NR",
                        "boardType": "RO",
                        "boardName": "Room only",
                        "retailRate": {"total": [{"amount": 189.0, "currency": "EUR"}]},
                        "cancellationPolicies": {"refundableTag": "NRFN"},
                    },
                ],
            }],
        }]}
    )
    candidates = source.check("lp12345", date(2026, 9, 12), date(2026, 9, 14), 2, 0)
    assert len(candidates) == 2

    refundable = next(c for c in candidates if c.refundable)
    assert refundable.total_price == Decimal("212.0")
    assert refundable.currency == "EUR"
    assert refundable.board_type == "BB"  # BI → BB
    assert refundable.deep_link is None  # detection-only feed

    nrfn = next(c for c in candidates if not c.refundable)
    assert nrfn.board_type == "RO"

    # request body assumptions (assumption 2 in the validation script)
    _, _, payload = source._client.calls[0]
    assert payload["hotelIds"] == ["lp12345"]
    assert payload["currency"] == "EUR"
    assert payload["guestNationality"] == "LV"
    assert payload["occupancies"] == [{"adults": 2, "children": []}]


def test_check_ignores_other_hotels(source):
    source._client = _FakeClient(
        rates={"data": [{"hotelId": "other", "roomTypes": [{"rates": [
            {"retailRate": {"total": [{"amount": 1, "currency": "EUR"}]}}
        ]}]}]}
    )
    assert source.check("lp12345", date(2026, 9, 12), date(2026, 9, 14), 2, 0) == []


def test_board_type_fallbacks():
    assert _board_type({"boardType": "HB"}) == "HB"
    assert _board_type({"boardType": "XX", "boardName": "Half board"}) == "HB"
    assert _board_type({"boardName": "Bed and breakfast"}) == "BB"
    assert _board_type({"boardName": "All-inclusive"}) == "AI"
    assert _board_type({}) is None


def _rate_env(hotel_id, rates):
    return {"data": [{"hotelId": hotel_id, "roomTypes": [{"rates": rates}]}]}


def test_non_included_taxes_are_added_to_total(source):
    """§7: LiteAPI `total` can exclude VAT (included=false, seen live on
    lp52d95) — the candidate total must be all-in or phantom drops appear."""
    source._client = _FakeClient(rates=_rate_env("lp1", [{
        "boardType": "RO",
        "retailRate": {
            "total": [{"amount": 122.54, "currency": "EUR"}],
            "taxesAndFees": [
                {"included": False, "description": "VAT", "amount": 14.7, "currency": "EUR"},
                {"included": True, "description": "City tax", "amount": 3.0, "currency": "EUR"},
            ],
        },
        "cancellationPolicies": {"refundableTag": "RFN"},
    }]))
    (c,) = source.check("lp1", date(2026, 10, 8), date(2026, 10, 10), 2, 0)
    assert c.total_price == Decimal("137.24")  # 122.54 + 14.70; included 3.00 NOT re-added


def test_cross_currency_fee_skips_rate(source):
    """A non-included fee in another currency can't be summed safely — the rate
    is dropped rather than under-priced."""
    source._client = _FakeClient(rates=_rate_env("lp1", [
        {
            "retailRate": {
                "total": [{"amount": 100.0, "currency": "EUR"}],
                "taxesAndFees": [{"included": False, "amount": 10.0, "currency": "USD"}],
            },
            "cancellationPolicies": {"refundableTag": "RFN"},
        },
        {
            "retailRate": {"total": [{"amount": 150.0, "currency": "EUR"}]},
            "cancellationPolicies": {"refundableTag": "RFN"},
        },
    ]))
    candidates = source.check("lp1", date(2026, 10, 8), date(2026, 10, 10), 2, 0)
    assert [c.total_price for c in candidates] == [Decimal("150.0")]
