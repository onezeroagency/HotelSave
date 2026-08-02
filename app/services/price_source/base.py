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


class PriceSource(ABC):
    @abstractmethod
    def resolve_hotel(
        self,
        name: str,
        city: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> list[HotelMatch]:
        """Name + city → the aggregator's hotel IDs, ranked (§6b)."""

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
