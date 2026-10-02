"""HYROX races via the site's public WordPress REST API.

IMPORTANT LIMITATION: the API exposes an `event` post type with the race name and
URL, but its `date` field is the WordPress *post* date and `acf` comes back
empty; the detail pages carry no ld+json either. Race dates only exist in the
JS-rendered race finder. Phase 1 therefore records these with `start = None` and
`date_status = "unavailable_phase1"` rather than passing off a post date as a
race date. Phase 2's headless fetcher fills them in.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from .. import normalize as nz
from ..models import DATE_UNAVAILABLE, Event

API = "https://hyrox.com/wp-json/wp/v2/event"

_HYROX_RE = re.compile(r"hyrox", re.I)


def _city_from_title(title: Optional[str]) -> Optional[str]:
    """'HYROX Dubai' -> Dubai; 'Virgin Active HYROX Cape Town ||' -> Cape Town.

    Heuristic: the location is whatever follows the HYROX brand token.
    """
    if not title:
        return None
    parts = _HYROX_RE.split(title)
    if len(parts) < 2:
        return None
    tail = parts[-1].strip(" -|/,:\u2013\u2014")
    tail = re.sub(r"\s+", " ", tail).strip()
    return tail or None


def to_event(item: dict, source: str, found_on: str) -> Optional[Event]:
    title = nz.clean_text((item.get("title") or {}).get("rendered"))
    source_url = nz.absolute_url(item.get("link"))
    if not title or not source_url:
        return None

    city = _city_from_title(title)
    return Event(
        title=title,
        source=source,
        source_url=source_url,
        start=None,
        end=None,
        date_status=DATE_UNAVAILABLE,
        sport="hyrox",
        event_subtypes=["hyrox"],
        city=city,
        address=city,
        ticket_url=source_url,
        found_on_url=found_on,
        scraped_at=nz.now_iso(),
    )


def collect(site: dict, fetcher, log: Callable[[str], None]) -> List[Event]:
    events: List[Event] = []
    per_page = int(site.get("per_page", 100))
    max_pages = int(site.get("max_pages", 3))

    for page in range(1, max_pages + 1):
        url = f"{API}?per_page={per_page}&page={page}"
        result = fetcher.get(url, delay=site.get("delay_seconds"))
        if not result.ok:
            # WordPress answers 400 for pages past the end; that is a clean stop.
            if result.status != 400:
                log(f"    page {page}: {result.error or result.status}")
            break

        items = result.json()
        if not isinstance(items, list) or not items:
            break
        for item in items:
            event = to_event(item, site["key"], url)
            if event:
                events.append(event)
        if len(items) < per_page:
            break

    log(f"    races: {len(events)} (no race dates available without JS - see module docstring)")
    return events
