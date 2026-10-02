"""Read the __NEXT_DATA__ payload that Next.js sites embed in their HTML."""

from __future__ import annotations

import json
import re
from typing import Optional

_NEXT_RE = re.compile(
    r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", re.S
)


def extract(html: Optional[str]) -> Optional[dict]:
    if not html:
        return None
    match = _NEXT_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def page_props(html: Optional[str]) -> dict:
    data = extract(html)
    if not isinstance(data, dict):
        return {}
    props = data.get("props")
    if not isinstance(props, dict):
        return {}
    page = props.get("pageProps")
    return page if isinstance(page, dict) else {}
