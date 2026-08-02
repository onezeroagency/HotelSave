"""Normalize a Postmark inbound webhook payload into an InboundEmail.

Postmark inbound JSON: https://postmarkapp.com/developer/webhooks/inbound-webhook
Only the fields we need are read; the raw payload can carry much more."""

from .base import InboundEmail


def parse_postmark(payload: dict) -> InboundEmail:
    from_full = payload.get("FromFull") or {}
    from_email = (from_full.get("Email") or payload.get("From") or "").strip().lower()
    return InboundEmail(
        from_email=from_email,
        subject=payload.get("Subject"),
        text_body=payload.get("TextBody") or payload.get("StrippedTextReply"),
        html_body=payload.get("HtmlBody"),
    )
