"""Map a schema.org Event node onto :class:`Event`.

Used for any site whose pages carry ld+json (AllEvents, District), so the field
variance lives here rather than in each site's config.
"""

from __future__ import annotations

from typing import Optional

from .. import normalize as nz
from ..models import Event


def to_event(node: dict, source: str, found_on: str) -> Optional[Event]:
    title = nz.clean_text(node.get("name"))
    source_url = nz.absolute_url(node.get("url"), found_on)
    if not title or not source_url:
        return None

    location = nz.first(node.get("location"))
    venue = address = city = region = country = None
    latitude = longitude = None
    if isinstance(location, dict):
        venue = nz.clean_text(location.get("name"))
        address, city, region, country = nz.flatten_address(
            location.get("address") or location
        )
        geo = location.get("geo")
        if isinstance(geo, dict):
            latitude = nz.parse_price(geo.get("latitude"))
            longitude = nz.parse_price(geo.get("longitude"))
    elif location is not None:
        venue = nz.clean_text(location)

    price_min, price_max, currency, ticket_url = nz.parse_offers(
        node.get("offers"), found_on
    )

    attendance = str(node.get("eventAttendanceMode") or "")
    organizer = nz.first(node.get("organizer"))
    if isinstance(organizer, dict):
        organizer = organizer.get("name")

    status = nz.clean_text(node.get("eventStatus"))
    if status:
        status = status.rsplit("/", 1)[-1]  # https://schema.org/EventScheduled

    # Sport hints: the schema subtype, any keywords, then the title.
    subtypes = [t for t in nz.coerce_list(node.get("@type")) if isinstance(t, str)]

    return Event(
        title=title,
        source=source,
        source_url=source_url,
        description=nz.clean_text(node.get("description"), limit=600),
        start=nz.parse_dt(node.get("startDate")),
        end=nz.parse_dt(node.get("endDate")),
        sport=nz.classify_sport(node.get("keywords"), title),
        event_subtypes=sorted({t for t in subtypes if t != "Event"}),
        is_online="Online" in attendance,
        venue_name=venue,
        address=address,
        city=city,
        region=region,
        country=country,
        latitude=latitude,
        longitude=longitude,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        ticket_url=ticket_url or source_url,
        found_on_url=found_on,
        image_url=nz.absolute_url(nz.first(node.get("image")), found_on),
        organizer=nz.clean_text(organizer),
        status=status,
        scraped_at=nz.now_iso(),
    )
