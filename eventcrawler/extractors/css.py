"""Selector-driven fallback for pages that publish no structured markup.

Driven entirely by a `selectors:` block in sites.yaml so a new site needs config,
not code.
"""

from __future__ import annotations

from typing import List, Optional

from bs4 import BeautifulSoup


def _text(node, selector: Optional[str]) -> Optional[str]:
    if not selector:
        return None
    element = node.select_one(selector)
    return element.get_text(" ", strip=True) if element else None


def _attr(node, selector: Optional[str], *names: str) -> Optional[str]:
    if not selector:
        return None
    element = node.select_one(selector)
    if element is None:
        return None
    for name in names:
        value = element.get(name)
        if value:
            return value
    return None


def extract(html: str, selectors: dict) -> List[dict]:
    container = (selectors or {}).get("container")
    if not container:
        return []
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for node in soup.select(container):
        row = {
            "title": _text(node, selectors.get("title")),
            "url": _attr(node, selectors.get("url"), "href"),
            "date": _attr(node, selectors.get("date"), "datetime")
            or _text(node, selectors.get("date")),
            "venue": _text(node, selectors.get("venue")),
            "price": _text(node, selectors.get("price")),
            "image": _attr(node, selectors.get("image"), "src", "data-src"),
            "distance": _text(node, selectors.get("distance")),
        }
        if row["title"]:
            rows.append(row)
    return rows
