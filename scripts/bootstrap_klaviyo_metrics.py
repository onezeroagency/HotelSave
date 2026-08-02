"""Bootstrap the HotelSave metrics in Klaviyo (§10).

Klaviyo creates a metric the first time it receives an event with that name, and
a flow trigger can't reference a metric that doesn't exist yet. This sends one
sample of every event the backend emits so all the metrics appear, ready to build
Flows 1 & 2 on top of (see docs/klaviyo-setup.md).

Usage:
    KLAVIYO_API_KEY=pk_... python scripts/bootstrap_klaviyo_metrics.py you@example.com

Runs against whichever account KLAVIYO_API_KEY belongs to — point it at your
HotelSave account. With no key set, events are logged (dry-run) instead of sent.
"""

import sys

from app.config import settings
from app.services import klaviyo

# One representative payload per event, using the exact property keys the backend
# sends (see app/services/klaviyo.py and app/services/ingestion.py).
SAMPLES: dict[str, dict] = {
    klaviyo.EVENT_BOOKING_MONITORED: {
        "hotel": "Amrita Hotel",
        "city": "Liepāja",
        "check_in": "2026-07-16",
        "check_out": "2026-07-18",
        "original_price": 225.0,
        "currency": "EUR",
        "cancellation_deadline": "2026-07-14T23:59:00",
    },
    klaviyo.EVENT_PRICE_DROP_FOUND: {
        "hotel": "Amrita Hotel",
        "old_price": 225.0,
        "new_price": 147.0,
        "savings_amount": 78.0,
        "savings_pct": 34.7,
        "rebook_url": "https://example.test/rebook?h=hotellook:amrita",
        "currency": "EUR",
        "cancellation_deadline": "2026-07-14T23:59:00",
    },
    klaviyo.EVENT_DEADLINE_APPROACHING: {
        "hotel": "Amrita Hotel",
        "cancellation_deadline": "2026-07-14T23:59:00",
        "checks_done": 6,
    },
    klaviyo.EVENT_MONITORING_ENDED: {
        "hotel": "Amrita Hotel",
        "best_savings_seen": 78.0,
        "outcome": "drop_found",
    },
    klaviyo.EVENT_SIGNUP_INVITE: {"subject": "Your booking is confirmed"},
    klaviyo.EVENT_NEEDS_REFUNDABLE: {"hotel": "The Nines", "check_in": "2026-09-17"},
    klaviyo.EVENT_NEEDS_DEADLINE: {"hotel": "Amrita Hotel"},
    klaviyo.EVENT_HOTEL_RESOLUTION_NEEDED: {"hotel": "Grand", "candidates": "[]"},
}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/bootstrap_klaviyo_metrics.py <email>")
    email = sys.argv[1]

    if not settings.klaviyo_api_key:
        print("KLAVIYO_API_KEY not set — events will be logged (dry-run), not sent.\n")

    for name, props in SAMPLES.items():
        klaviyo.emit_event(name, email, props)
        print(f"sent: {name}")

    print(f"\nDone. Check Analytics → Metrics in Klaviyo for the {len(SAMPLES)} metrics.")


if __name__ == "__main__":
    main()
