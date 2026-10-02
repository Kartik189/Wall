"""IndiaRunning: sitemap -> event detail pages -> Next.js __NEXT_DATA__.

The homepage embeds only the first 12 events and its ?page=N pagination is
client-side (the server returns an empty list), so the sitemap is the only
reliable way to enumerate every published event.

Detail pages are also richer than the listing: they carry latitude/longitude and
per-category prices and distances, which the listing omits. Two different shapes
exist, so both are mapped:
  listing: eventsData.events[] -> eventDate{start,end}, locationInfo, price
  detail : eventInfo           -> eventDate (string), location, categories[]
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from .. import normalize as nz
from ..extractors import nextdata
from ..models import Event

SITEMAP = "https://indiarunning.com/sitemap.xml"
URL_TEMPLATE = "https://www.indiarunning.com/events/{slug}"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def event_url(slug: Optional[str]) -> Optional[str]:
    """The feed exposes only a slug. Verified: /events/<slug> is the live path
    (/event/<slug> and /<slug> both 404), and the canonical host is www."""
    slug = nz.clean_text(slug)
    return nz.absolute_url(URL_TEMPLATE.format(slug=slug)) if slug else None


def _geo(location):
    if not isinstance(location, dict):
        return None, None
    return nz.parse_price(location.get("latitude")), nz.parse_price(location.get("longitude"))


def _about_text(info: dict) -> Optional[str]:
    block = nz.first(info.get("aboutRace"))
    if isinstance(block, dict):
        return nz.clean_text(block.get("content"), limit=600)
    return nz.clean_text(block, limit=600)


def from_detail(info: dict, source: str, found_on: str) -> Optional[Event]:
    title = nz.clean_text(info.get("title"))
    source_url = event_url(info.get("slug")) or nz.absolute_url(found_on)
    if not title or not source_url:
        return None

    location = info.get("location") or {}
    address, city, region, country = nz.flatten_address(location)
    latitude, longitude = _geo(location)

    categories = [c for c in (info.get("categories") or []) if isinstance(c, dict)]
    labels, kms = nz.parse_distances(
        (c.get("distance") or {}).get("distance") for c in categories
    )
    price_min, price_max = nz.price_range(
        (c.get("price") or {}).get("value") for c in categories
    )
    currency = next(
        (
            nz.clean_text((c.get("price") or {}).get("currency"))
            for c in categories
            if (c.get("price") or {}).get("currency")
        ),
        None,
    )

    return Event(
        title=title,
        source=source,
        source_url=source_url,
        description=_about_text(info),
        start=nz.parse_dt(info.get("eventDate")),
        end=nz.parse_dt(info.get("endDate")),
        sport=nz.classify_sport([c.get("sportName") for c in categories], title),
        distances=labels,
        distance_km=kms,
        event_subtypes=sorted(
            {nz.clean_text(c.get("title")) for c in categories if c.get("title")}
        ),
        venue_name=nz.clean_text(location.get("area")),
        address=address,
        city=city,
        region=region,
        country=country,
        latitude=latitude,
        longitude=longitude,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        ticket_url=source_url,
        found_on_url=found_on,
        image_url=nz.absolute_url(nz.first(info.get("bannerImageUrl"))),
        scraped_at=nz.now_iso(),
    )


def from_listing(item: dict, source: str, found_on: str) -> Optional[Event]:
    source_url = event_url(item.get("slug"))
    if not source_url:
        return None
    title = nz.clean_text(item.get("title") or item.get("name"))
    if not title:  # listing rows sometimes omit a title; fall back to the slug
        title = nz.clean_text(str(item.get("slug", "")).replace("_", " ").replace("-", " "))
    if not title:
        return None

    location = item.get("locationInfo") or {}
    address, city, region, country = nz.flatten_address(location)
    categories = [c for c in (item.get("categories") or []) if isinstance(c, dict)]
    labels, kms = nz.parse_distances(c.get("category") for c in categories)
    dates = item.get("eventDate")
    dates = dates if isinstance(dates, dict) else {"start": dates, "end": None}

    return Event(
        title=title,
        source=source,
        source_url=source_url,
        start=nz.parse_dt(dates.get("start")),
        end=nz.parse_dt(dates.get("end")),
        sport=nz.classify_sport(item.get("sportsType"), title),
        distances=labels,
        distance_km=kms,
        event_subtypes=sorted({c.get("category") for c in categories if c.get("category")}),
        venue_name=nz.clean_text(location.get("area")),
        address=address,
        city=city,
        region=region,
        country=country,
        price_min=nz.parse_price(item.get("price")),
        price_max=nz.parse_price(item.get("price")),
        currency=nz.clean_text(item.get("currency")),
        ticket_url=source_url,
        found_on_url=found_on,
        image_url=nz.absolute_url(nz.first(item.get("imageUrls"))),
        organizer=nz.clean_text(item.get("orgName")),
        scraped_at=nz.now_iso(),
    )


def _sitemap_event_urls(fetcher, log) -> List[str]:
    result = fetcher.get(SITEMAP)
    if not result.ok:
        log(f"    sitemap: {result.error or result.status}")
        return []
    seen = set()
    ordered = []
    for url in _LOC_RE.findall(result.text):
        if "/events/" in url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def collect(site: dict, fetcher, log: Callable[[str], None]) -> List[Event]:
    by_url = {}

    # 1. Listing seeds: cheap, and a safety net if the sitemap ever moves.
    for seed in site.get("seeds") or []:
        result = fetcher.get(seed, delay=site.get("delay_seconds"))
        if not result.ok:
            log(f"    {seed}: {result.error or result.status}")
            continue
        data = nextdata.page_props(result.text).get("eventsData")
        rows = data.get("events") if isinstance(data, dict) else data
        for item in rows or []:
            if isinstance(item, dict):
                event = from_listing(item, site["key"], seed)
                if event:
                    by_url.setdefault(event.source_url, event)
    log(f"    listing: {len(by_url)}")

    # 2. Sitemap -> detail pages, which add geo, distances and per-category prices.
    if site.get("use_sitemap", True):
        max_detail = int(site.get("max_detail_pages", 40))
        detail_urls = _sitemap_event_urls(fetcher, log)[:max_detail]
        enriched = 0
        for url in detail_urls:
            result = fetcher.get(url, delay=site.get("delay_seconds"))
            if not result.ok:
                continue
            info = nextdata.page_props(result.text).get("eventInfo")
            if not isinstance(info, dict):
                continue
            event = from_detail(info, site["key"], url)
            if event:
                by_url[event.source_url] = event  # detail supersedes listing
                enriched += 1
        log(f"    sitemap detail: {enriched} of {len(detail_urls)} pages")

    return list(by_url.values())
