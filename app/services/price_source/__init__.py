"""Price source (§9): the aggregator lives behind this interface so swapping a
prototype source for the real commission-based aggregator is a one-line change."""

from ...config import settings
from .base import HotelMatch, PriceSource, RateCandidate
from .mock import MockPriceSource

__all__ = ["HotelMatch", "PriceSource", "RateCandidate", "get_price_source"]


def get_price_source() -> PriceSource:
    """Factory selected by settings.price_source. Add real sources here."""
    name = settings.price_source.lower()
    if name == "mock":
        return MockPriceSource()
    if name == "hotellook":
        from .hotellook import HotellookPriceSource  # §9 — Travelpayouts / Hotellook

        return HotellookPriceSource()
    raise ValueError(f"Unknown PRICE_SOURCE: {settings.price_source!r}")
