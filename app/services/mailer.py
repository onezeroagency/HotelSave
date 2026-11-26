"""Outbound transactional email via Postmark.

Klaviyo owns lifecycle email (flows, copy, timing); this mailer exists for
the one message that must carry a server-minted secret and arrive within
seconds: the magic login link. Without POSTMARK_SERVER_TOKEN it dry-runs to
the log and reports success, same contract as the Klaviyo client, so the
rest of the system can be exercised without a live account.
"""

import logging

import httpx

from ..config import settings

logger = logging.getLogger("hotelsave.mailer")

POSTMARK_EMAIL_URL = "https://api.postmarkapp.com/email"


def send_email(to: str, subject: str, html_body: str, text_body: str) -> bool:
    """Send one transactional email. Returns True when accepted (or
    deliberately dry-run), False when delivery failed — callers that tell the
    user "check your inbox" should log loudly on False."""
    if not settings.postmark_server_token:
        logger.info("[postmark:dry-run] to=%s subject=%r\n%s", to, subject, text_body)
        return True

    payload = {
        "From": settings.postmark_from_email,
        "To": to,
        "Subject": subject,
        "HtmlBody": html_body,
        "TextBody": text_body,
        "MessageStream": settings.postmark_message_stream,
    }
    headers = {
        "X-Postmark-Server-Token": settings.postmark_server_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(POSTMARK_EMAIL_URL, json=payload, headers=headers, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError:  # pragma: no cover - network path
        logger.exception("Postmark send failed for %s (%r)", to, subject)
        return False
    return True


def magic_link_email(link: str, expires_minutes: int) -> tuple[str, str, str]:
    """(subject, html, text) for the magic sign-in email."""
    subject = "Your MyRoomWatch sign-in link"
    text = (
        f"Tap to sign in to MyRoomWatch:\n\n{link}\n\n"
        f"The link works once and expires in {expires_minutes} minutes.\n\n"
        "Next step: forward any refundable hotel confirmation to "
        "save@myroomwatch.com from this email address, and we'll start "
        "watching it.\n\n"
        "Didn't ask for this? Ignore this email - nothing changes without "
        "the link.\n\n"
        "MyRoomWatch · SIA ONEZERO, Latvia, EU · hello@myroomwatch.com"
    )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F4F6F8;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;padding:32px;">
        <tr><td style="font-size:18px;font-weight:bold;color:#0D2137;padding-bottom:20px;">MyRoomWatch</td></tr>
        <tr><td style="font-size:22px;font-weight:bold;color:#0D2137;padding-bottom:12px;">Sign in with one tap</td></tr>
        <tr><td style="font-size:15px;line-height:1.6;color:#3C4A5A;padding-bottom:24px;">
          This link signs you in to MyRoomWatch. It works once and expires in
          {expires_minutes} minutes.
        </td></tr>
        <tr><td style="padding-bottom:24px;">
          <a href="{link}" style="display:inline-block;background:#0A7A52;color:#ffffff;font-size:16px;font-weight:bold;text-decoration:none;padding:14px 26px;border-radius:12px;">Open my dashboard</a>
        </td></tr>
        <tr><td style="font-size:14px;line-height:1.6;color:#3C4A5A;border-top:1px solid #E5EAEF;padding-top:20px;padding-bottom:20px;">
          <strong>Next step:</strong> forward any refundable hotel confirmation
          to <strong>save@myroomwatch.com</strong> from this email address, and
          we'll start watching it before your free-cancellation deadline.
        </td></tr>
        <tr><td style="font-size:13px;line-height:1.6;color:#6B7A8A;">
          Didn't ask for this? Ignore this email — nothing changes without the
          link.
        </td></tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;padding:16px 8px;">
        <tr><td style="font-size:12px;color:#8A97A5;line-height:1.6;">
          MyRoomWatch · SIA ONEZERO, Latvia, EU · hello@myroomwatch.com
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return subject, html, text
