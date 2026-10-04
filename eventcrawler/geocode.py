"""Give every event a latitude/longitude so the viewer can filter by distance.

Only some sources publish coordinates (IndiaRunning, AllEvents). The rest give a
city at best, so those are geocoded to the city centre through OpenStreetMap's
Nominatim and flagged `coords_source = "geocoded"` - a distance computed from
them is approximate, and the viewer says so.

Nominatim's usage policy asks for at most one request per second, an honest
User-Agent and caching of results; all three are done here. Lookups are keyed
by query and persisted in .cache/geocode.json, so after the first run only
newly-seen cities cost a request. Network failures are never cached as "not
found", and a run that cannot reach the service at all gives up after a few
attempts rather than stalling on every event.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

from . import normalize as nz
from .models import COORDS_GEOCODED, COORDS_LISTING, Event

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "EventCrawler/0.1 (local endurance-event viewer)"

#: Bump whenever the accept/reject rules change, so stale verdicts are dropped.
CACHE_VERSION = 3

#: A match wider than this (corner to corner) is a state or a country, not a
#: place anyone can measure "10 km away" from. Judged by size rather than by
#: OSM's place type, because city-states - Singapore, Hong Kong, Delhi,
#: Shanghai - are typed as countries or states yet are compact enough. Big
#: municipalities with outlying islands (Shanghai 262 km, Incheon 251 km) must
#: pass; whole states (Punjab ~445 km, Gujarat ~820 km) must not.
_MAX_EXTENT_KM = 400.0
_TOO_COARSE = {"country", "state", "continent", "region"}

#: Consecutive network failures after which the service is assumed unreachable,
#: and how long before it is tried again.
_MAX_FAILURES = 3
_RETRY_AFTER_SECONDS = 60.0

SearchFn = Callable[[dict], list]


class Geocoder:
    def __init__(
        self,
        cache_path: Optional[Path] = None,
        search: Optional[SearchFn] = None,
        min_interval: float = 1.1,
        timeout: int = 20,
    ) -> None:
        self.cache_path = Path(cache_path) if cache_path else None
        self.min_interval = min_interval
        self.timeout = timeout
        self._search = search or self._http_search
        self._session: Optional[requests.Session] = None
        self._cache: Dict[str, Optional[dict]] = self._load_cache()
        self._last_request = 0.0
        self._failures = 0
        self._last_failure = 0.0
        self._lock = threading.Lock()
        self.requests_made = 0
        #: Set when the most recent lookup failed on the network rather than
        #: finding nothing - lets callers tell "unknown place" from "offline".
        self.last_error: Optional[str] = None

    # -- cache -------------------------------------------------------------- #
    def _load_cache(self) -> Dict[str, Optional[dict]]:
        if not (self.cache_path and self.cache_path.exists()):
            return {}
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}
        # Entries were accepted or rejected under the rules of their version.
        if not isinstance(data, dict) or data.get("_version") != CACHE_VERSION:
            return {}
        data.pop("_version", None)
        return data

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump({"_version": CACHE_VERSION, **self._cache}, handle,
                      indent=1, ensure_ascii=False, sort_keys=True)

    # -- lookup ------------------------------------------------------------- #
    @property
    def reachable(self) -> bool:
        # The cooldown lets the long-running viewer recover from a network blip.
        return (
            self._failures < _MAX_FAILURES
            or time.monotonic() - self._last_failure > _RETRY_AFTER_SECONDS
        )

    def _http_search(self, params: dict) -> list:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        response = self._session.get(NOMINATIM_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def lookup(self, query: str, country: Optional[str] = None) -> Optional[dict]:
        """Resolve a place name to {"lat", "lon", "label"}, or None if unknown.

        `country` is an ISO-2 code; when given, results outside it are ignored,
        which keeps "Aurangabad, IN" from landing in Bihar's namesake abroad.
        """
        query = nz.clean_text(query)
        if not query:
            return None
        code = country.lower() if country and len(country) == 2 and country.isalpha() else None
        key = f"{query.lower()}|{code or ''}"

        with self._lock:
            if key in self._cache:
                self.last_error = None
                return self._cache[key]
            if not self.reachable:
                self.last_error = "geocoding service unreachable"
                return None

            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            params = {"q": query, "format": "jsonv2", "limit": 1}
            if code:
                params["countrycodes"] = code
            try:
                self.requests_made += 1
                results = self._search(params)
            except (requests.RequestException, ValueError) as exc:
                self._failures += 1
                self._last_failure = time.monotonic()
                self.last_error = f"{type(exc).__name__}: {exc}"
                return None
            finally:
                self._last_request = time.monotonic()
            self._failures = 0
            self.last_error = None

            hit = None
            if results and _precise_enough(results[0]):
                top = results[0]
                try:
                    hit = {
                        "lat": round(float(top["lat"]), 6),
                        "lon": round(float(top["lon"]), 6),
                        "label": top.get("display_name") or query,
                    }
                except (KeyError, TypeError, ValueError):
                    hit = None
            self._cache[key] = hit
            return hit


def _extent_km(bbox) -> Optional[float]:
    """Diagonal of a Nominatim boundingbox [south, north, west, east]."""
    try:
        south, north, west, east = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    height = (north - south) * 111.0
    width = (east - west) * 111.0 * math.cos(math.radians((north + south) / 2))
    return math.hypot(height, width)


def _precise_enough(result: dict) -> bool:
    extent = _extent_km(result.get("boundingbox"))
    if extent is not None:
        return extent <= _MAX_EXTENT_KM
    return result.get("addresstype") not in _TOO_COARSE


def _queries(event: Event) -> Iterable[str]:
    """Most specific first: "city, region", then the city alone, because state
    abbreviations ("GJ") and odd region spellings make the first form miss."""
    city = nz.clean_text(event.city)
    if not city:
        return []
    region = nz.clean_text(event.region)
    queries = [f"{city}, {region}"] if region and region.lower() != city.lower() else []
    queries.append(city)
    return queries


def annotate(
    events: List[Event],
    geocoder: Geocoder,
    log: Callable[[str], None] = lambda msg: None,
) -> Tuple[int, int]:
    """Fill latitude/longitude where a source left them empty.

    Coordinates a source stated are never replaced. Returns
    (geocoded, still_missing); `still_missing` counts events with no
    coordinates afterwards, typically ones with no city at all.
    """
    geocoded = 0
    pending = [e for e in events if e.latitude is None or e.longitude is None]
    for event in events:
        if event.latitude is not None and event.longitude is not None and not event.coords_source:
            event.coords_source = COORDS_LISTING

    if pending:
        log(f"\n[geocode] {len(pending)} events without coordinates")
    for event in pending:
        for query in _queries(event):
            hit = geocoder.lookup(query, event.country)
            if hit:
                event.latitude, event.longitude = hit["lat"], hit["lon"]
                event.coords_source = COORDS_GEOCODED
                geocoded += 1
                break
        if not geocoder.reachable:
            log("    geocoding service unreachable - remaining events left without coordinates")
            break

    geocoder.save()
    missing = sum(1 for e in events if e.latitude is None or e.longitude is None)
    if pending:
        log(f"    {geocoded} geocoded, {missing} still without coordinates "
            f"({geocoder.requests_made} lookups, rest from cache)")
    return geocoded, missing
