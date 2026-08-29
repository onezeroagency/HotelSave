"""The "check today's price" link (§9 mode A)."""

from datetime import date
from urllib.parse import parse_qs, urlparse

from app.config import settings
from app.models import MonitoringJob
from app.services.rebook import check_prices_url


def _job(**kw):
    defaults = dict(
        id=1,
        hotel_name_raw="Grand Poet Hotel and SPA by Semarah",
        city="Riga",
        check_in=date(2026, 9, 26),
        check_out=date(2026, 9, 28),
        adults=2,
        children=0,
    )
    defaults.update(kw)
    return MonitoringJob(**defaults)


def _params(url):
    return parse_qs(urlparse(url).query)


def test_link_carries_the_whole_stay():
    p = _params(check_prices_url(_job()))
    assert p["checkin"] == ["2026-09-26"]
    assert p["checkout"] == ["2026-09-28"]
    assert p["group_adults"] == ["2"]
    assert "Grand Poet" in p["ss"][0] and "Riga" in p["ss"][0]


def test_affiliate_params_are_appended_when_configured(monkeypatch):
    """The day a program is approved this is the only change needed."""
    monkeypatch.setattr(settings, "rebook_affiliate_params", "aid=304142&label=mrw")
    p = _params(check_prices_url(_job()))
    assert p["aid"] == ["304142"]
    assert p["label"] == ["mrw"]
    assert p["checkin"] == ["2026-09-26"]  # still a real search, not just a tag


def test_affiliate_params_override_our_defaults(monkeypatch):
    """A program's required keys must win, not be silently dropped."""
    monkeypatch.setattr(settings, "rebook_affiliate_params", "?group_adults=9")
    assert _params(check_prices_url(_job()))["group_adults"] == ["9"]


def test_link_works_without_an_affiliate_account(monkeypatch):
    """Mode A ships before approval — the link just earns nothing meanwhile."""
    monkeypatch.setattr(settings, "rebook_affiliate_params", None)
    url = check_prices_url(_job())
    assert url and url.startswith("https://")


def test_no_link_without_a_hotel_name():
    """A search with no property lands on a generic results page, which looks
    like we found something. Better to send nothing."""
    assert check_prices_url(_job(hotel_name_raw="")) is None


def test_no_link_when_the_base_url_is_unset(monkeypatch):
    monkeypatch.setattr(settings, "rebook_search_base_url", "")
    assert check_prices_url(_job()) is None
