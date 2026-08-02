"""Deterministic parser for local dev and tests — no API key required.

Reads a simple `Key: value` format so tests can exercise every ingestion branch
(refundable or not, deadline present or missing, hotel ambiguous or resolved)
without an LLM. Real emails go through ClaudeParser."""

import re
from datetime import date, datetime, timedelta

from ...enums import BoardType
from .base import BookingParser, ParsedBooking


def _find(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in ("yes", "true", "y", "1", "refundable")


class MockParser(BookingParser):
    def parse(self, raw_email_text: str) -> ParsedBooking | None:
        text = raw_email_text or ""

        board_raw = _find(text, "Board")
        deadline_raw = _find(text, "Cancellation")
        adults_raw = _find(text, "Adults")
        children_raw = _find(text, "Children")
        price_raw = _find(text, r"(?:Total|Price)")

        return ParsedBooking(
            hotel_name=_find(text, "Hotel") or "Test Hotel",
            city=_find(text, "City"),
            country=_find(text, "Country"),
            check_in=self._date(_find(text, "Check-?in"), default_days=30),
            check_out=self._date(_find(text, "Check-?out"), default_days=33),
            room_type_raw=_find(text, "Room"),
            board_type=BoardType(board_raw) if board_raw in BoardType.__members__ else None,
            adults=int(adults_raw) if adults_raw and adults_raw.isdigit() else None,
            children=int(children_raw) if children_raw and children_raw.isdigit() else None,
            total_price=float(price_raw) if price_raw else 300.0,
            currency=(_find(text, "Currency") or "EUR").upper(),
            ota=_find(text, "OTA"),
            refundable=_to_bool(_find(text, "Refundable")),
            cancellation_deadline=self._datetime(deadline_raw),
        )

    @staticmethod
    def _date(value: str | None, default_days: int) -> date:
        if value:
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        return date.today() + timedelta(days=default_days)

    @staticmethod
    def _datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
