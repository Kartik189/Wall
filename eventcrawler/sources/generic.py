"""Map loosely-shaped objects from JavaScript payloads onto Event records.

Unlike schema.org, a site's internal JSON has no agreed vocabulary - one calls
it `startDate`, the next `event_date`, a third `beginsAt`. This module matches
keys against alias groups and only accepts an object once it looks convincingly
like an event, so a page's navigation config does not become 40 bogus races.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from .. import normalize as nz
from ..extractors.jsonld import EVENT_TYPES
from ..models import Event
from . import schemaorg

#: Normalised key -> field group. Keys are compared with punctuation and case
#: stripped, so `start_date`, `startDate` and `StartDate` all collapse together.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "title": ("name", "title", "eventname", "eventtitle", "racename", "displayname",
              "heading", "label", "producttitle"),
    "start": ("startdate", "start", "date", "eventdate", "startsat", "eventstartdate",
              "begindate", "starttime", "startdatetime", "racedate", "datetime",
              "fromdate", "eventstart"),
    "end": ("enddate", "end", "finishdate", "eventenddate", "endsat", "todate",
            "enddatetime", "eventend"),
    "url": ("url", "link", "eventurl", "permalink", "href", "weburl", "pageurl",
            "detailurl", "canonicalurl", "eventlink", "linkurl"),
    "slug": ("slug", "seourl", "urlslug", "eventslug", "eventcode", "friendlyurl"),
    "venue": ("venue", "venuename", "place", "placename", "locationname", "eventvenue",
              "address", "addressline1", "fulladdress"),
    "city": ("city", "cityname", "town", "locality", "eventcity"),
    "region": ("state", "region", "province", "statename", "administrativearea"),
    "country": ("country", "countryname", "countrycode", "nation"),
    "price": ("price", "fee", "cost", "amount", "minprice", "ticketprice",
              "startingprice", "eventminprice", "lowestprice", "pricefrom",
              "registrationfee", "entryfee"),
    "price_max": ("maxprice", "eventmaxprice", "highestprice", "priceto"),
    "currency": ("currency", "currencycode", "pricecurrency"),
    "image": ("image", "imageurl", "banner", "bannerimage", "bannerimageurl",
              "thumbnail", "poster", "photo", "coverimage", "featuredimage"),
    "description": ("description", "about", "summary", "details", "shortdescription",
                    "eventdescription", "overview"),
    "distance": ("distance", "distances", "category", "categories", "racecategories",
                 "eventcategories", "racetypes", "subevents", "tickets", "categorylist"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
    "organizer": ("organizer", "organiser", "host", "organizername", "organisername",
                  "promoter"),
    "online": ("isonline", "online", "isvirtual", "virtual"),
}

#: Reverse lookup built once: normalised key -> group name.
_GROUP_BY_KEY: Dict[str, str] = {
    key: group for group, keys in ALIASES.items() for key in keys
}

#: Keys that identify a node as something other than an event. A blog post and
#: a race both have a title, a date and a link; only the post has an author. A
#: city has a name and a slug, but it counts races rather than being one.
DISQUALIFYING_KEYS = frozenset({
    "author", "authorname", "byline", "publishedat", "publisheddate", "publishedon",
    "postid", "posttype", "excerpt", "readtime", "readingtime", "wordcount",
    "commentcount", "comments", "racecount", "upcomingracecount", "eventcount",
    "itemcount", "productcount", "count", "totalcount", "views", "viewcount",
})

#: An object needs a title plus this many other recognised groups to qualify.
MIN_SUPPORTING_GROUPS = 2
#: Guards against walking a pathologically nested payload.
MAX_DEPTH = 12
#: How far below a node to look for its date and location. Sites routinely park
#: those one level down (`race.edition.event_start_date`).
NESTED_DEPTH = 2
#: Per page, so one bad match cannot flood the dataset.
MAX_EVENTS_PER_PAGE = 500


def _norm_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _groups(node: dict) -> Dict[str, Any]:
    """Collapse a dict's keys onto field groups, keeping the first value seen."""
    found: Dict[str, Any] = {}
    for key, value in node.items():
        group = _GROUP_BY_KEY.get(_norm_key(key))
        if group is None or value in (None, "", [], {}):
            continue
        found.setdefault(group, value)
    return found


def _deep_groups(node: dict, depth: int = NESTED_DEPTH) -> Dict[str, Any]:
    """`_groups`, plus anything found in nested objects.

    Shallower values win, so a nested organizer's `name` can never displace the
    event's own. Lists are not followed: a list of dicts is usually sibling
    records, and borrowing a neighbour's date would invent data.
    """
    found = _groups(node)
    if depth <= 0:
        return found
    for key, value in node.items():
        if not isinstance(value, dict):
            continue
        if _norm_key(key) in DISQUALIFYING_KEYS:
            continue
        for group, sub_value in _deep_groups(value, depth - 1).items():
            found.setdefault(group, sub_value)
    return found


def _is_schema_event(node: dict) -> bool:
    raw = node.get("@type") or node.get("type")
    types = raw if isinstance(raw, list) else [raw]
    return any(isinstance(t, str) and t in EVENT_TYPES for t in types)


def looks_like_event(node: Any) -> bool:
    """Accept only objects carrying a title, a real date, and enough corroboration.

    The date requirement is the load-bearing one. Without it a city - which has
    a name, a slug and a country - is indistinguishable from a thinly-described
    race, and a listing page's filter sidebar turns into hundreds of phantom
    events. Anything an event listing is actually worth having also has a date.
    """
    if not isinstance(node, dict):
        return False
    if any(_norm_key(key) in DISQUALIFYING_KEYS for key in node):
        return False

    # The title has to be the object's own, not one borrowed from a child.
    title = _groups(node).get("title")
    if not isinstance(title, str) or not (3 <= len(title.strip()) <= 250):
        return False

    groups = _deep_groups(node)
    if not nz.parse_dt(nz.first(groups.get("start"))):
        return False
    if not (groups.keys() & {"url", "slug"}):
        return False
    return len([g for g in groups if g != "title"]) >= MIN_SUPPORTING_GROUPS


def iter_event_like(data: Any, depth: int = 0) -> Iterator[dict]:
    """Walk a parsed payload, yielding dicts that look like events.

    A matching dict is still descended into: sites nest sub-events (a race day
    holding its individual distances) and both levels can be worth keeping.
    """
    if depth > MAX_DEPTH:
        return
    if isinstance(data, dict):
        if _is_schema_event(data) or looks_like_event(data):
            yield data
        for value in data.values():
            yield from iter_event_like(value, depth + 1)
    elif isinstance(data, list):
        for item in data:
            yield from iter_event_like(item, depth + 1)


def _text_hints(groups: Dict[str, Any]) -> List[str]:
    """Free text the sport classifier can work from."""
    hints: List[str] = []
    for key in ("distance", "title", "description"):
        value = groups.get(key)
        if isinstance(value, str):
            hints.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    hints.append(item)
                elif isinstance(item, dict):
                    sub = _groups(item)
                    for candidate in ("title", "distance"):
                        if isinstance(sub.get(candidate), str):
                            hints.append(sub[candidate])
    return hints


def _distance_values(value: Any) -> List[Any]:
    """Distances arrive as a string, a list of strings, or a list of objects."""
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        return [value]
    if isinstance(value, list):
        out: List[Any] = []
        for item in value:
            if isinstance(item, dict):
                sub = _groups(item)
                out.extend(v for v in (sub.get("distance"), sub.get("title")) if v)
            else:
                out.append(item)
        return out
    return []


def to_event(
    node: dict,
    source: str,
    found_on: str,
    base_url: Optional[str] = None,
    url_template: Optional[str] = None,
) -> Optional[Event]:
    """Build an Event from a loose object, or None if it cannot be identified."""
    if _is_schema_event(node):
        return schemaorg.to_event(node, source, found_on)

    groups = _deep_groups(node)
    title = nz.clean_text(_groups(node).get("title"))
    if not title:
        return None

    base = base_url or found_on
    source_url = nz.absolute_url(nz.first(groups.get("url")), base)
    if not source_url and url_template and groups.get("slug"):
        slug = nz.clean_text(groups["slug"])
        if slug:
            source_url = url_template.format(slug=slug)
    if not source_url:
        return None

    labels, kms = nz.parse_distances(_distance_values(groups.get("distance")))
    price_min, price_max = nz.price_range(
        [nz.parse_price(groups.get("price")), nz.parse_price(groups.get("price_max"))]
    )

    return Event(
        title=title,
        source=source,
        source_url=source_url,
        description=nz.clean_text(groups.get("description"), 600),
        start=nz.parse_dt(nz.first(groups.get("start"))),
        end=nz.parse_dt(nz.first(groups.get("end"))),
        sport=nz.classify_sport(*_text_hints(groups)),
        distances=labels,
        distance_km=kms,
        is_online=bool(groups.get("online")),
        venue_name=nz.clean_text(nz.first(groups.get("venue")), 200),
        city=nz.clean_text(groups.get("city")),
        region=nz.clean_text(groups.get("region")),
        country=nz.clean_text(groups.get("country")),
        latitude=nz.parse_price(groups.get("latitude")),
        longitude=nz.parse_price(groups.get("longitude")),
        price_min=price_min,
        price_max=price_max,
        currency=nz.clean_text(groups.get("currency")),
        ticket_url=source_url,
        found_on_url=found_on,
        image_url=nz.absolute_url(nz.first(groups.get("image")), base),
        organizer=nz.clean_text(nz.first(groups.get("organizer")), 200),
        scraped_at=nz.now_iso(),
    )


def from_payloads(
    payloads: List[Tuple[str, Any]],
    source: str,
    found_on: str,
    url_template: Optional[str] = None,
) -> Tuple[List[Event], Dict[str, int]]:
    """Map every event-shaped object in the payloads. Returns (events, per-label counts)."""
    events: List[Event] = []
    counts: Dict[str, int] = {}
    seen: set = set()

    for label, data in payloads:
        for node in iter_event_like(data):
            if len(events) >= MAX_EVENTS_PER_PAGE:
                return events, counts
            event = to_event(node, source, found_on, found_on, url_template)
            if event is None or event.source_url in seen:
                continue
            seen.add(event.source_url)
            events.append(event)
            counts[label] = counts.get(label, 0) + 1

    return events, counts
