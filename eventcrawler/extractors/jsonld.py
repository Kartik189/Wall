"""Extract schema.org Event objects from application/ld+json script blocks.

The walk is recursive because sources nest events differently: AllEvents puts
Event at the top level, while District buries them under
ItemList > itemListElement > ListItem > item.
"""

from __future__ import annotations

import json
import re
from typing import Iterator, List

from bs4 import BeautifulSoup

EVENT_TYPES = {
    "Event",
    "SportsEvent",
    "MusicEvent",
    "TheaterEvent",
    "ComedyEvent",
    "Festival",
    "ScreeningEvent",
    "EducationEvent",
    "BusinessEvent",
    "SocialEvent",
    "FoodEvent",
    "ExhibitionEvent",
    "DanceEvent",
    "ChildrensEvent",
    "LiteraryEvent",
    "SaleEvent",
    "VisualArtsEvent",
}

_LDJSON_RE = re.compile(r"ld\+json", re.I)


def _is_event(node: dict) -> bool:
    raw = node.get("@type")
    types = raw if isinstance(raw, list) else [raw]
    for value in types:
        if isinstance(value, str) and (value in EVENT_TYPES or value.endswith("Event")):
            return True
    return False


def _walk(node) -> Iterator[dict]:
    if isinstance(node, dict):
        if _is_event(node):
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def iter_documents(html: str) -> Iterator[object]:
    """Yield each parsed ld+json document; a malformed block is skipped, not fatal."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", attrs={"type": _LDJSON_RE}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            # Some sites emit trailing commas; one cheap repair attempt.
            try:
                yield json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue


def extract(html: str) -> List[dict]:
    """All Event-ish nodes on the page, de-duplicated by (name, url)."""
    found: List[dict] = []
    seen = set()
    for document in iter_documents(html):
        for node in _walk(document):
            key = (node.get("name"), node.get("url"))
            if key != (None, None) and key in seen:
                continue
            seen.add(key)
            found.append(node)
    return found
