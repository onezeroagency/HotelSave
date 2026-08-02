"""Inbound email (§5): a forward-to address, not Gmail OAuth. Providers differ
only in their webhook payload shape, so each provider normalizes into the common
`InboundEmail` and the rest of the system is provider-agnostic."""

from .base import InboundEmail
from .postmark import parse_postmark

__all__ = ["InboundEmail", "parse_postmark"]
