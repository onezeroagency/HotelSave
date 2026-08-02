"""Booking parser (§6a): one LLM call turns a forwarded confirmation email into
structured data. Behind an interface so the model/provider is swappable and the
whole ingestion path is testable with a deterministic mock."""

from ...config import settings
from .base import BookingParser, ParsedBooking

__all__ = ["BookingParser", "ParsedBooking", "get_parser"]


def get_parser() -> BookingParser:
    """Factory selected by settings.parser. Falls back to the mock when Claude
    isn't configured, so local dev and tests never need an API key."""
    if settings.parser.lower() == "claude" and settings.anthropic_api_key:
        from .claude import ClaudeParser

        return ClaudeParser()

    from .mock import MockParser

    return MockParser()
