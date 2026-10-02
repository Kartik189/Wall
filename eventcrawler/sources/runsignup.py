"""RunSignup public REST API (no API key required).

Endpoint: https://runsignup.com/rest/races
Verified event_type values: running_race, trail_race, walking_only, triathlon,
duathlon, bike_race, swim, adventure_race.

A race is one record with N sub-events (the 5K, the 10K, ...), so distances and
subtypes are aggregated up and the earliest sub-event start becomes `start`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, List, Optional
from urllib.parse import urlencode

from .. import normalize as nz
from ..models import Event

API = "https://runsignup.com/rest/races"


def _earliest_start(sub_events: list) -> Optional[str]:
    starts = [nz.parse_dt(e.get("start_time")) for e in sub_events if isinstance(e, dict)]
    valid = sorted(s for s in starts if s)
    return valid[0] if valid else None


def _fees(sub_events: list) -> list:
    """Registration fees live inside each sub-event's registration_periods."""
    values = []
    for event in sub_events:
        for period in (event.get("registration_periods") or []):
            if isinstance(period, dict):
                values.append(period.get("race_fee"))
    return values


def to_event(race: dict, source: str, found_on: str, event_type: str) -> Optional[Event]:
    title = nz.clean_text(race.get("name"))
    source_url = nz.absolute_url(race.get("url"))
    if not title or not source_url:
        return None

    address_data = race.get("address") or {}
    address, city, region, country = nz.flatten_address(address_data)

    sub_events = [e for e in (race.get("events") or []) if isinstance(e, dict)]
    subtypes = sorted({e.get("event_type") for e in sub_events if e.get("event_type")})
    labels, kms = nz.parse_distances(e.get("distance") for e in sub_events)

    price_min, price_max = nz.price_range(_fees(sub_events))

    return Event(
        title=title,
        source=source,
        source_url=source_url,
        description=nz.clean_text(race.get("description"), limit=600),
        start=_earliest_start(sub_events) or nz.parse_dt(race.get("next_date")),
        end=nz.parse_dt(race.get("next_end_date")),
        sport=nz.classify_sport(subtypes, event_type, title),
        distances=labels,
        distance_km=kms,
        event_subtypes=subtypes,
        venue_name=nz.clean_text(address_data.get("street")),
        address=address,
        city=city,
        region=region,
        country=country,
        price_min=price_min,
        price_max=price_max,
        currency="USD" if price_min is not None else None,
        ticket_url=nz.absolute_url(race.get("external_race_url")) or source_url,
        found_on_url=found_on,
        image_url=nz.absolute_url(race.get("logo_url")),
        scraped_at=nz.now_iso(),
    )


def _window(site: dict) -> tuple:
    """Only future races, out to `days_ahead`, unless the config pins the dates."""
    today = date.today()
    date_from = site.get("date_from") or today.strftime("%Y-%m-%d")
    date_to = site.get("date_to")
    if not date_to and site.get("days_ahead"):
        date_to = (today + timedelta(days=int(site["days_ahead"]))).strftime("%Y-%m-%d")
    return date_from, date_to


def collect(site: dict, fetcher, log: Callable[[str], None]) -> List[Event]:
    events: List[Event] = []
    per_page = int(site.get("results_per_page", 50))
    max_pages = int(site.get("max_pages_per_type", 2))
    event_types = site.get("event_types") or ["running_race"]

    for event_type in event_types:
        found_for_type = 0
        for page in range(1, max_pages + 1):
            params = {
                "format": "json",
                "results_per_page": per_page,
                "page": page,
                "events": "T",
                "event_type": event_type,
            }
            date_from, date_to = _window(site)
            if date_from:
                params["race_event_days_from"] = date_from
            if date_to:
                params["race_event_days_to"] = date_to

            url = f"{API}?{urlencode(params)}"
            result = fetcher.get(url, delay=site.get("delay_seconds"))
            if not result.ok:
                log(f"    {event_type} page {page}: {result.error or result.status}")
                break

            races = (result.json() or {}).get("races") or []
            if not races:
                break
            for wrapper in races:
                event = to_event(wrapper.get("race") or {}, site["key"], url, event_type)
                if event:
                    events.append(event)
                    found_for_type += 1
            if len(races) < per_page:
                break
        log(f"    {event_type}: {found_for_type}")
    return events
