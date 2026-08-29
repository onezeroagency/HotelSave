"""The PriceSource interface plus the plain data shapes it returns.

Every real aggregator (Travelpayouts / Hotellook, etc.) implements this. The rest
of the system only ever talks to `PriceSource` — never to a vendor SDK directly."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class HotelMatch:
    """A candidate hotel returned by the lookup step (§6b)."""

    hotel_id: str
    name: str
    city: str | None
    country: str | None
    lat: float | None
    lng: float | None
    confidence: float  # 0..1 — used to decide auto-accept vs. ask-the-user


@dataclass(frozen=True)
class RateCandidate:
    """A single bookable rate for a stay, from some OTA."""

    ota: str
    total_price: Decimal
    currency: str
    board_type: str | None
    adults: int | None
    children: int | None
    refundable: bool
    deep_link: str | None  # affiliate rebook link (§9)
    # What the provider calls this room ("Standard Double Room"). Kept so §7 can
    # reject a cheaper *different* room and so an alert can name what it found —
    # without it, diagnosing a suspicious drop means re-querying the provider by
    # hand. Providers that don't name rooms leave it None, which never blocks a
    # match on its own.
    room_name: str | None = None


class PriceSource(ABC):
    @abstractmethod
    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        country: str | None = None,
    ) -> list[HotelMatch]:
        """Name + city (+ country) → the aggregator's hotel IDs, ranked (§6b).

        `country` is an ISO-3166 alpha-2 code from the booking. Some providers
        require it to scope a city search at all (LiteAPI 400s without one), so
        callers should pass it through whenever the booking states it.
        """

    @abstractmethod
    def check(
        self,
        hotel_id: str,
        check_in: date,
        check_out: date,
        adults: int | None,
        children: int | None,
    ) -> list[RateCandidate]:
        """All bookable rates for the stay — the like-for-like filter (§7) is
        applied by the caller, not here."""
