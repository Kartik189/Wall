"""Regression tests for the embedded-JavaScript extraction path.

Runs without a network: `python tests/test_js_extraction.py`

The acceptance heuristic in sources/generic.py is the risky part of this
feature - too loose and every nav link becomes a race. The negative cases below
matter as much as the positive ones.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eventcrawler.extractors import jsvars
from eventcrawler.sources import generic

BS = chr(92)
ESC = BS + '"'


def _js_string(pairs):
    """Build a JS-escaped JSON object literal, as it would appear inside a script."""
    body = ",".join(f"{ESC}{k}{ESC}:{ESC}{v}{ESC}" for k, v in pairs)
    return "{" + body + "}"


FIXTURE = """<html><head>
<script type="application/ld+json">{"@type":"Event","name":"Owned by the JSON-LD extractor"}</script>
<script type="application/json" id="__NEXT_DATA__">{"props":{"pageProps":{"events":[
  {"title":"Next Data Race","startDate":"2027-03-01","slug":"next-data-race","city":"Pune"}]}}}</script>
<script>window.__NUXT__ = {"data":[{"races":[
  {"name":"Nuxt Trail Ultra","date":"2027-04-11","url":"/r/nuxt-trail","venue":"Lonavala"}]}]};</script>
<script>var flags = {"padding":4,"theme":"dark"};
const listings = [{"eventName":"Assigned Swim Meet","eventDate":"2027-05-05","link":"/swim/meet",
  "location":"Goa","price":"500","organizer":"Goa Aquatics",
  "description":"An annual open water swim meet along the Goa coastline."}];</script>
<script>self.__next_f.push([1,"3:FLIGHT" + BS + "n"])</script>
<script>var blob = JSON.parse("PARSE");</script>
</head></html>""".replace("BS", repr(BS)).replace(' + ' + repr(BS) + ' + "n"', BS + 'n"')

FIXTURE = FIXTURE.replace(
    "FLIGHT",
    "{" + ESC + "events" + ESC + ":[" + _js_string(
        [("title", "Flight Duathlon"), ("startDate", "2027-06-02"),
         ("href", "/d/flight"), ("venue", "Manali")]) + "]}",
).replace(
    "PARSE",
    "{" + ESC + "items" + ESC + ":[" + _js_string(
        [("title", "Parsed Cycling Sportive"), ("date", "2027-07-09"),
         ("url", "/c/sportive"), ("city", "Coorg")]) + "]}",
)

FAILURES = []


def check(condition, message):
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        FAILURES.append(message)


def test_patterns_found():
    labels = {label for label, _ in jsvars.extract(FIXTURE)}
    for expected in ("script[type=json]", "__NUXT__", "listings", "__next_f", "JSON.parse"):
        check(expected in labels, f"pattern discovered: {expected}")
    check("flags" not in labels, "tiny config object rejected by size floor")


def test_events_mapped():
    events, counts = generic.from_payloads(
        jsvars.extract(FIXTURE), "demo", "https://demo.test/events",
        url_template="https://demo.test/events/{slug}",
    )
    by_title = {e.title: e for e in events}
    check(len(events) == 5, f"all five payloads mapped (got {len(events)})")
    check(
        by_title.get("Next Data Race") is not None
        and by_title["Next Data Race"].source_url == "https://demo.test/events/next-data-race",
        "slug resolved through url_template",
    )
    check(
        by_title.get("Nuxt Trail Ultra") is not None
        and by_title["Nuxt Trail Ultra"].source_url == "https://demo.test/r/nuxt-trail",
        "relative url made absolute against the page",
    )
    check(by_title.get("Flight Duathlon", None) is not None
          and by_title["Flight Duathlon"].sport == "duathlon", "sport classified from title")
    check(by_title.get("Assigned Swim Meet", None) is not None
          and by_title["Assigned Swim Meet"].price_min == 500.0, "price parsed")
    check(all(e.source_url for e in events), "every event carries a link")
    check(len(counts) == 5, "each pattern credited in the per-label counts")


def test_rejects_non_events():
    cases = [
        ({"name": "Home", "url": "/"}, "nav item"),
        ({"title": "Contact Us", "href": "/contact"}, "single-signal nav link"),
        ({"padding": 4, "theme": "dark"}, "config object"),
        ({"title": "A", "startDate": "2027-01-01", "city": "Y", "url": "/a"}, "title too short"),
        ({"eventTitle": None, "eventStartDate": None, "linkUrl": "/x"}, "null-valued banner"),
        # The cases that made racesindia.com emit 255 events per page.
        ({"name": "Bengaluru", "slug": "bengaluru", "country": "India", "state": "Karnataka",
          "race_count": 84, "upcoming_race_count": 12}, "city in a filter sidebar"),
        ({"name": "Chennai", "slug": "chennai", "country": "India", "state": "Tamil Nadu"},
         "place with no date"),
        ({"title": "Road to Aqaba: Indian Squad Locked for Asian Championships",
          "publishedAt": "2026-08-02", "url": "/news/aqaba", "author": "ITF Desk"},
         "news article"),
        ({"title": "Half Marathon", "url": "/categories/half"}, "race category, no date"),
    ]
    for node, why in cases:
        accepted = generic._is_schema_event(node) or generic.looks_like_event(node)
        check(not accepted, f"rejected: {why}")

    good = {"title": "Pune Half Marathon", "startDate": "2027-02-01", "city": "Pune",
            "url": "/e/pune-half"}
    check(generic.looks_like_event(good), "accepted: title + date + city + link")


def test_nested_fields():
    """Real payloads park the date a level down (racesindia: race.edition.*)."""
    node = {
        "name": "Sohna By CapitalTrails",
        "slug": "sohna-by-capitaltrails",
        "edition": {
            "event_start_date": "2026-11-01",
            "distances": ["10 km", "25 km", "50 km"],
            "location": {"city": "Aravalli Retreat", "state": "Haryana", "country": "India"},
        },
    }
    check(generic.looks_like_event(node), "accepted via a nested date")
    event = generic.to_event(node, "demo", "https://demo.test/races",
                             url_template="https://demo.test/races/{slug}")
    check(event is not None, "nested node maps to an event")
    if event:
        check(str(event.start).startswith("2026-11-01"), f"nested date read (got {event.start})")
        check(event.city == "Aravalli Retreat", f"nested city read (got {event.city})")
        check(event.country == "India", f"nested country read (got {event.country})")
        check("50K" in event.distances, f"nested distances read (got {event.distances})")

    # A child's name must never be promoted to the event's title.
    borrowed = {"organizer": {"name": "CapitalTrails India"}, "startDate": "2026-11-01",
                "url": "/x"}
    check(not generic.looks_like_event(borrowed), "rejected: title only present on a child")


def test_scanner_is_string_aware():
    check(jsvars._scan_literal('x = {"a":{"b":[1,2]},"c":"}"} tail', 4) == '{"a":{"b":[1,2]},"c":"}"}',
          "brace inside a string does not end the object")
    check(jsvars._scan_literal('y = [1,[2,3],"]"] tail', 4) == '[1,[2,3],"]"]',
          "bracket inside a string does not end the array")
    check(jsvars._scan_literal("z = {unterminated", 4) is None, "unterminated literal returns None")


if __name__ == "__main__":
    for test in (test_patterns_found, test_events_mapped, test_rejects_non_events,
                 test_nested_fields, test_scanner_is_string_aware):
        print(test.__name__)
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s)")
        raise SystemExit(1)
    print("all checks passed")
