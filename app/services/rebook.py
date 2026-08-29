"""Where we send the user to act (§9, "mode A").

The detection feed and the channel the user booked on are different markets, so
we cannot promise "this exact rate, here". What we *can* do honestly is hand
them a search for their own stay on a real OTA and let them see today's price
themselves — a prompt to check, not a claimed figure.

This is deliberately a job-level link (hotel + dates + occupancy), not a
rate-level one. A rate-level deep link would imply we can deliver a specific
price, which is exactly the promise we can't keep until detection and rebooking
share a channel (see docs/price-source-migration.md).

Affiliate attribution is a query-string add-on: set REBOOK_AFFILIATE_PARAMS once
a program is approved and every link starts earning without a code change. With
it unset the link still works — it just earns nothing, which is the right
trade while approval is pending.
"""

import logging
from urllib.parse import parse_qsl, urlencode

from ..config import settings

logger = logging.getLogger("hotelsave.rebook")


def check_prices_url(job) -> str | None:
    """A link to today's prices for this stay, or None if we can't build one."""
    base = (settings.rebook_search_base_url or "").strip()
    if not base:
        return None

    hotel = (job.hotel_name_raw or "").strip()
    if not hotel:
        # Without a property name the search would land on a generic results
        # page — worse than no link, because it looks like we found something.
        logger.warning("Job %s has no hotel name; skipping the check-prices link", job.id)
        return None

    query = {
        "ss": f"{hotel} {job.city}".strip() if job.city else hotel,
        "checkin": job.check_in.isoformat(),
        "checkout": job.check_out.isoformat(),
        "group_adults": job.adults or 2,
        "group_children": job.children or 0,
        "no_rooms": 1,
    }
    # Affiliate params are operator-supplied and must win, so a program's own
    # required keys can override our defaults rather than being silently dropped.
    extra = (settings.rebook_affiliate_params or "").strip().lstrip("?")
    if extra:
        query.update(dict(parse_qsl(extra, keep_blank_values=True)))

    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{urlencode(query)}"
