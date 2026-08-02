"""The parsed-booking shape (matches the §6a schema) and the parser interface."""

from abc import ABC, abstractmethod
from datetime import date, datetime

from pydantic import BaseModel

from ...enums import BoardType


class ParsedBooking(BaseModel):
    """Structured output of the parse step. `None` means "not clearly stated" —
    the parser must not guess (§6a). Also used directly as the LLM's structured-
    output schema, so keep the fields JSON-schema friendly (no Decimal)."""

    hotel_name: str
    city: str | None = None
    country: str | None = None
    address: str | None = None
    check_in: date
    check_out: date
    room_type_raw: str | None = None
    board_type: BoardType | None = None
    adults: int | None = None
    children: int | None = None
    total_price: float
    currency: str
    ota: str | None = None
    refundable: bool | None = None
    cancellation_deadline: datetime | None = None


class BookingParser(ABC):
    @abstractmethod
    def parse(self, raw_email_text: str) -> ParsedBooking | None:
        """Extract a booking from raw email text, or None if it can't be parsed."""
