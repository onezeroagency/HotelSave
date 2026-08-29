"""Claude-backed parser: one structured-output call per inbound booking (§6a).

Uses the Anthropic SDK's `messages.parse()`, which enforces the ParsedBooking
schema server-side and returns a validated instance — no prose, no markdown-fence
stripping, no hand-rolled JSON parsing."""

import logging

import anthropic

from ...config import settings
from .base import BookingParser, ParsedBooking

logger = logging.getLogger("hotelsave.parser")

SYSTEM_PROMPT = (
    "You extract structured hotel booking data from a forwarded confirmation "
    "email. Use null for anything not clearly stated. Do not guess. Dates are "
    "ISO 8601. Currency is an ISO 4217 code (e.g. EUR). board_type is one of "
    "RO (room only), BB (bed & breakfast), HB (half board), FB (full board).\n"
    # The deadline is the actionability clock: everything downstream is timed
    # against it, and erring late is the one error the user cannot recover from
    # — they'd act after free cancellation has already closed.
    "cancellation_deadline: when the wording is a boundary rather than an "
    "instant, always resolve it to the EARLIEST moment it could mean, never the "
    "latest. 'Free cancellation before 25 September' means the deadline is the "
    "end of 24 September, not any time on the 25th. If a time of day is not "
    "stated, assume the start of the day, not the end of it. Never output a "
    "deadline later than the email supports.\n"
    "total_price: the all-in total the guest actually pays for the whole stay, "
    "including taxes and fees when the email states an inclusive total. It is "
    "compared against tax-inclusive rates elsewhere, so a per-night or "
    "pre-tax figure makes the comparison wrong.\n"
    "room_type_raw: copy the room name exactly as written ('Standard Double "
    "Room'). It decides whether a cheaper rate is the same product."
)


class ClaudeParser(BookingParser):
    def __init__(self) -> None:
        # Identity-linked API keys (as opposed to workspace-scoped ones) are
        # rejected with a 400 unless the request names the workspace it acts in:
        #   "anthropic-workspace-id is required when authenticating with an
        #    identity-linked API key"
        # Set ANTHROPIC_WORKSPACE_ID for those keys; workspace-scoped keys need
        # nothing and the header is simply omitted.
        headers = (
            {"anthropic-workspace-id": settings.anthropic_workspace_id}
            if settings.anthropic_workspace_id
            else None
        )
        # api_key defaults to ANTHROPIC_API_KEY / an `ant auth login` profile when
        # settings.anthropic_api_key is None.
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, default_headers=headers
        )
        self._model = settings.claude_model

    def parse(self, raw_email_text: str) -> ParsedBooking | None:
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Email:\n<<<\n{raw_email_text}\n>>>",
                    }
                ],
                output_format=ParsedBooking,
            )
        except Exception:  # pragma: no cover - network / API path
            logger.exception("Claude parse call failed")
            return None

        if response.stop_reason == "refusal":
            logger.warning("Claude declined to parse the email")
            return None
        return response.parsed_output
