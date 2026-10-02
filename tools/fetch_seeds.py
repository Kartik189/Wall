"""Cache every candidate page using the crawler's own UA, retrying past the
intermittent TLS interception on this network.

The UA matters: several sites (Townscript) route crawler user agents to a
prerender service and return fully-rendered HTML with schema.org markup, while
a browser UA gets an empty SPA shell.
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import requests

from eventcrawler.fetcher import DEFAULT_UA

CACHE = ROOT / ".cache/probe"
ATTEMPTS = 6


def path_for(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")


def grab(url):
    path = path_for(url)
    if path.exists() and path.stat().st_size:
        return url, "cached"
    last = "?"
    for attempt in range(ATTEMPTS):
        try:
            response = requests.get(
                url, timeout=30, headers={"User-Agent": DEFAULT_UA}, allow_redirects=True
            )
            if response.status_code == 200:
                path.write_text(response.text, encoding="utf-8")
                return url, "200"
            if 400 <= response.status_code < 500:
                return url, f"HTTP {response.status_code}"
            last = f"HTTP {response.status_code}"
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1.0 + attempt)
    return url, last


if __name__ == "__main__":
    urls = []
    for name in (ROOT / "tools/seed_candidates.txt", ROOT / "tools/seed_candidates2.txt"):
        urls += [line.strip() for line in open(name, encoding="utf-8") if line.strip()]
    urls = list(dict.fromkeys(urls))

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(grab, urls))

    index = dict(results)
    json.dump(index, open(CACHE / "index.json", "w"), indent=1)
    got = sum(1 for _, s in results if s in ("200", "cached"))
    print(f"{got}/{len(results)} cached")
