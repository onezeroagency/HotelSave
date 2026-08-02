"""Tests for the Booking.com Demand API price source scaffold (§9).

Only the stable plumbing is asserted here — base-URL selection, header auth, and
credential guards. The resolve_hotel/check mapping is intentionally unimplemented
(pending sandbox validation), so those are asserted to raise NotImplementedError.
Replace those two with real room→candidate mapping tests when the endpoints are
wired against the sandbox — see docs/price-source-migration.md §8."""

from datetime import date

import pytest

from app.config import settings
from app.services.price_source.booking import BASE_URLS, BookingPriceSource


@pytest.fixture()
def source(monkeypatch):
    monkeypatch.setattr(settings, "booking_api_key", "test-key")
    monkeypatch.setattr(settings, "booking_affiliate_id", "123456")
    monkeypatch.setattr(settings, "booking_env", "sandbox")
    return BookingPriceSource()


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.setattr(settings, "booking_api_key", None)
    monkeypatch.setattr(settings, "booking_affiliate_id", None)
    with pytest.raises(RuntimeError):
        BookingPriceSource()


def test_sandbox_is_the_default_base_url(source):
    assert source._base_url == BASE_URLS["sandbox"]
    assert "sandbox" in source._base_url


def test_production_base_url_selected(monkeypatch):
    monkeypatch.setattr(settings, "booking_api_key", "k")
    monkeypatch.setattr(settings, "booking_affiliate_id", "1")
    monkeypatch.setattr(settings, "booking_env", "production")
    assert BookingPriceSource()._base_url == BASE_URLS["production"]


def test_invalid_env_raises(monkeypatch):
    monkeypatch.setattr(settings, "booking_api_key", "k")
    monkeypatch.setattr(settings, "booking_affiliate_id", "1")
    monkeypatch.setattr(settings, "booking_env", "staging")
    with pytest.raises(ValueError):
        BookingPriceSource()


def test_auth_headers(source):
    headers = source._auth_headers()
    assert headers["X-Affiliate-Id"] == "123456"
    assert headers["Authorization"] == "Bearer test-key"


def test_resolve_and_check_not_implemented_yet(source):
    # These guard the scaffold: they must be replaced with real mapping tests once
    # the endpoints are validated against the sandbox.
    with pytest.raises(NotImplementedError):
        source.resolve_hotel("Amrita Hotel", "Liepaja")
    with pytest.raises(NotImplementedError):
        source.check("123", date(2026, 9, 10), date(2026, 9, 13), 2, 0)
