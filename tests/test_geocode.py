"""Tests for coordinate lookup (eventcrawler/geocode.py).

Runs without a network: `python tests/test_geocode.py`. Nominatim is replaced by
a fake search function so the cache, fallback and failure handling can be
checked deterministically.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from eventcrawler.geocode import Geocoder, annotate
from eventcrawler.models import COORDS_GEOCODED, COORDS_LISTING, Event

FAILURES = []


def check(condition, message):
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        FAILURES.append(message)


PLACES = {
    "pune": {"lat": "18.5213738", "lon": "73.8545071", "display_name": "Pune, Maharashtra, India",
             "addresstype": "city", "boundingbox": ["18.36", "18.68", "73.69", "74.01"]},
    "rajsamand, rajasthan": {"lat": "25.2892459", "lon": "73.8241077",
                             "display_name": "Rajsamand, Rajasthan, India",
                             "addresstype": "state_district",
                             "boundingbox": ["24.77", "25.98", "73.36", "74.29"]},
    "ahmedabad": {"lat": "23.0215374", "lon": "72.5800568", "display_name": "Ahmedabad, India",
                  "addresstype": "city", "boundingbox": ["22.87", "23.19", "72.45", "72.71"]},
    "nowhereville": {"lat": "20.0", "lon": "78.0", "display_name": "India", "addresstype": "country",
                     "boundingbox": ["6.5", "35.7", "68.1", "97.4"]},
    "hong kong": {"lat": "22.35", "lon": "114.18", "display_name": "Hong Kong, China",
                  "addresstype": "country", "boundingbox": ["22.15", "22.56", "113.83", "114.44"]},
    "gujarat": {"lat": "22.38", "lon": "71.74", "display_name": "Gujarat, India",
                "addresstype": "state", "boundingbox": ["20.12", "24.71", "68.16", "74.48"]},
    "shanghai": {"lat": "31.23", "lon": "121.47", "display_name": "Shanghai, China",
                 "addresstype": "state", "boundingbox": ["30.67", "31.88", "120.85", "122.2"]},
    "no box": {"lat": "1.0", "lon": "2.0", "display_name": "No Box", "addresstype": "city"},
}


class FakeSearch:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, params):
        self.calls.append(params)
        if self.fail:
            raise requests.ConnectionError("offline")
        hit = PLACES.get(params["q"].lower())
        return [hit] if hit else []


def _event(title, **kwargs):
    return Event(title=title, source="test", source_url=f"https://example.com/{title}", **kwargs)


def _geocoder(search, cache_path=None):
    return Geocoder(cache_path=cache_path, search=search, min_interval=0)


def test_annotate():
    search = FakeSearch()
    listed = _event("listed", city="Bengaluru", latitude=12.97, longitude=77.59)
    by_city = _event("by-city", city="Pune", country="IN")
    by_region = _event("by-region", city="Rajsamand", region="Rajasthan", country="IN")
    region_miss = _event("region-miss", city="Ahmedabad", region="GJ", country="IN")
    no_city = _event("no-city", region="Kerala")
    too_coarse = _event("too-coarse", city="Nowhereville")

    events = [listed, by_city, by_region, region_miss, no_city, too_coarse]
    geocoded, missing = annotate(events, _geocoder(search))

    check(listed.coords_source == COORDS_LISTING, "listing coordinates are marked as such")
    check((listed.latitude, listed.longitude) == (12.97, 77.59), "listing coordinates never replaced")
    check(by_city.coords_source == COORDS_GEOCODED and abs(by_city.latitude - 18.52) < 0.01,
          "city-only event geocoded")
    check(abs(by_region.latitude - 25.29) < 0.01, "city + region query resolved")
    check(abs(region_miss.latitude - 23.02) < 0.01,
          "falls back to the city alone when 'city, region' misses")
    check(no_city.latitude is None and no_city.coords_source is None,
          "no city means no lookup (a state centre is too coarse to measure from)")
    check(too_coarse.latitude is None, "a country-wide match is rejected")
    check((geocoded, missing) == (3, 2), f"counts geocoded/missing (got {geocoded}/{missing})")
    check(all(c.get("countrycodes") == "in" for c in search.calls if "Pune" in c["q"]),
          "country code restricts the search")


def test_extent_rule():
    geocoder = _geocoder(FakeSearch())
    check(geocoder.lookup("Hong Kong") is not None,
          "compact city-state accepted even though OSM types it a country")
    check(geocoder.lookup("Gujarat") is None, "a whole state is too wide to measure from")
    check(geocoder.lookup("Shanghai") is not None, "a big municipality with outlying islands passes")
    check(geocoder.lookup("No Box") is not None, "falls back to place type without a bounding box")


def test_hyrox_city():
    from eventcrawler.sources.hyrox import _city_from_title

    for title, want in [
        ("HYROX Dubai", "Dubai"),
        ("Virgin Active HYROX Cape Town ||", "Cape Town"),
        ("HYROX Youngstars Paris", "Paris"),
        ("PUMA HYROX World Championships Hong Kong", "Hong Kong"),
    ]:
        got = _city_from_title(title)
        check(got == want, f"{title!r} -> {want!r} (got {got!r})")


def test_cache():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "geocode.json"
        first = FakeSearch()
        annotate([_event("a", city="Pune", country="IN")], _geocoder(first, path))
        check(path.exists(), "cache written to disk")

        second = FakeSearch()
        again = _event("b", city="Pune", country="IN")
        annotate([again], _geocoder(second, path))
        check(not second.calls, "second run answered from the cache")
        check(again.latitude is not None, "cached hit applied")

        third = FakeSearch()
        annotate([_event("c", city="Nowhereville")], _geocoder(third, path))
        annotate([_event("d", city="Nowhereville")], _geocoder(third, path))
        check(len(third.calls) == 1, "a genuine miss is cached too")

        data = json.loads(path.read_text(encoding="utf-8"))
        data["_version"] = -1
        path.write_text(json.dumps(data), encoding="utf-8")
        fourth = FakeSearch()
        annotate([_event("e", city="Pune", country="IN")], _geocoder(fourth, path))
        check(len(fourth.calls) == 1, "a cache from older matching rules is discarded")


def test_network_failure():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "geocode.json"
        offline = FakeSearch(fail=True)
        geocoder = _geocoder(offline, path)
        events = [_event(f"e{i}", city=f"City{i}") for i in range(10)]
        geocoded, missing = annotate(events, geocoder)
        check((geocoded, missing) == (0, 10), "nothing located while offline")
        check(len(offline.calls) == 3, f"gives up after 3 failures (made {len(offline.calls)})")
        check(geocoder.last_error and "offline" in geocoder.last_error,
              "network error is reported, not treated as not-found")

        online = FakeSearch()
        event = _event("later", city="Pune")
        annotate([event], _geocoder(online, path))
        check(online.calls and event.latitude is not None, "failures were not cached as misses")


if __name__ == "__main__":
    for test in (test_annotate, test_extent_rule, test_hyrox_city, test_cache, test_network_failure):
        print(test.__name__)
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        raise SystemExit(1)
    print("all checks passed")
