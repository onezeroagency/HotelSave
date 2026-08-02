"""The normalized inbound email shape all providers map into."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundEmail:
    from_email: str
    subject: str | None
    text_body: str | None
    html_body: str | None

    @property
    def body(self) -> str:
        """Best available body for parsing — prefer plain text over HTML."""
        return self.text_body or self.html_body or ""
