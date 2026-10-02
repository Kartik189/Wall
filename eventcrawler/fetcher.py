"""Polite HTTP fetching: robots.txt, per-host crawl delay, retries, optional cache.

Failures are returned, never raised: a blocked site (BookMyShow/Ironman return
403) has to show up in the run report with its status code rather than killing
the crawl.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 EventCrawler/0.1"
)


@dataclass
class FetchResult:
    url: str
    status: Optional[int]
    text: Optional[str] = None
    error: Optional[str] = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.text is not None

    def json(self):
        if not self.text:
            return None
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return None


class Fetcher:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        timeout: int = 25,
        default_delay: float = 2.0,
        respect_robots: bool = True,
        cache_dir: Optional[Path] = None,
        use_cache: bool = False,
        retries: int = 2,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.default_delay = default_delay
        self.respect_robots = respect_robots
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.use_cache = use_cache
        self.retries = retries

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            }
        )
        self._robots: Dict[str, Optional[RobotFileParser]] = {}
        self._last_request: Dict[str, float] = {}
        self.request_count = 0

        if self.use_cache and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- robots ------------------------------------------------------------ #
    def _robots_for(self, url: str) -> Optional[RobotFileParser]:
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]

        parser: Optional[RobotFileParser] = None
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        try:
            # Fetched with our own session so the site sees a real UA.
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            parser = None  # unreachable robots.txt is treated as "no rules"
        self._robots[host] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def robots_delay(self, url: str) -> Optional[float]:
        """Honour a host's Crawl-delay (allevents.in asks for 10s)."""
        parser = self._robots_for(url)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
        except AttributeError:
            return None
        return float(delay) if delay else None

    # -- cache -------------------------------------------------------------- #
    def _cache_path(self, url: str) -> Optional[Path]:
        if not (self.use_cache and self.cache_dir):
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    # -- fetching ----------------------------------------------------------- #
    def _throttle(self, url: str, delay: Optional[float]) -> None:
        host = urlparse(url).netloc
        wait = delay if delay is not None else self.default_delay
        robots_wait = self.robots_delay(url)
        if robots_wait:
            wait = max(wait, robots_wait)
        last = self._last_request.get(host)
        if last is not None:
            remaining = wait - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, delay: Optional[float] = None) -> FetchResult:
        cache_path = self._cache_path(url)
        if cache_path and cache_path.exists():
            return FetchResult(url, 200, cache_path.read_text(encoding="utf-8"), from_cache=True)

        if not self.can_fetch(url):
            return FetchResult(url, None, error="blocked by robots.txt")

        last_error = None
        for attempt in range(self.retries + 1):
            self._throttle(url, delay)
            try:
                self.request_count += 1
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue

            if response.status_code == 200:
                if cache_path:
                    cache_path.write_text(response.text, encoding="utf-8")
                return FetchResult(url, 200, response.text)

            # 4xx is a decision by the site: retrying will not change it.
            if 400 <= response.status_code < 500:
                return FetchResult(url, response.status_code, error=f"HTTP {response.status_code}")
            last_error = f"HTTP {response.status_code}"
            time.sleep(1.5 * (attempt + 1))

        return FetchResult(url, None, error=last_error or "request failed")
