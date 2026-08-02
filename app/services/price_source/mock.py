"""In-memory mock price source for local dev and tests.

Deterministic: keyed off the hotel_id string so the same job produces stable
output run-to-run. Swap for the real aggregator via PRICE_SOURCE (see __init__)."""

from datetime import date
from decimal import Decimal

from .base import HotelMatch, PriceSource, RateCandidate


class MockPriceSource(PriceSource):
    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[HotelMatch]:
        slug = "-".join(name.lower().split())[:40] or "hotel"
        # Without a city we can't disambiguate — return a weak multi-match so the
        # caller falls into the "ask the user which hotel" branch (§6b).
        if not city:
            return [
                HotelMatch(
                    hotel_id=f"mock:{slug}:{i}",
                    name=f"{name} ({i})",
                    city=None,
                    country=None,
                    lat=lat,
                    lng=lng,
                    confidence=0.5,
                )
                for i in (1, 2)
            ]
        return [
            HotelMatch(
                hotel_id=f"mock:{slug}",
                name=name,
                city=city,
                country=None,
                lat=lat,
                lng=lng,
                confidence=0.95,
            )
        ]

    def check(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> list[RateCandidate]:
        # Cheap deterministic pseudo-price so the scheduler has something to chew on.
        seed = sum(ord(c) for c in hotel_id) % 60
        base = Decimal(120 + seed)
        return [
            RateCandidate(
                ota="Booking.com",
                total_price=base,
                currency="EUR",
                board_type="BB",
                adults=adults,
                children=children,
                refundable=True,
                deep_link=f"https://example.test/rebook?h={hotel_id}",
            ),
            RateCandidate(
                ota="Expedia",
                total_price=base + Decimal(15),
                currency="EUR",
                board_type="BB",
                adults=adults,
                children=children,
                refundable=False,  # excluded by §7 rule 3
                deep_link=f"https://example.test/rebook?h={hotel_id}&ota=expedia",
            ),
        ]
