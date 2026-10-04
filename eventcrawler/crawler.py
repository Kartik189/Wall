"""Crawl every enabled source in sites.yaml and write output/events.json.

Sources differ enough that each gets a handler, chosen by the `type` field in
config:
    jsonld   - fetch listing pages, read schema.org markup (AllEvents)
    api      - call a JSON endpoint (RunSignup, HYROX)
    nextdata - read a Next.js __NEXT_DATA__ payload (IndiaRunning)
    css      - selector fallback for anything unstructured
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import yaml

from . import geocode
from . import normalize as nz
from .extractors import css as css_extractor
from .extractors import jsonld
from .extractors import jsvars
from .fetcher import Fetcher
from .geocode import Geocoder
from .models import COORDS_GEOCODED, Event
from .report import (
    STATUS_BLOCKED,
    STATUS_CARRIED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    RunReport,
    SiteResult,
)
from .sources import generic, hyrox, indiarunning, runsignup, schemaorg

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
EVENTS_PATH = OUTPUT_DIR / "events.json"
REPORT_PATH = OUTPUT_DIR / "run_report.json"
GEOCODE_CACHE = ROOT / ".cache" / "geocode.json"

#: Sources whose `type` is `api`/`nextdata` dispatch to a module by key.
SOURCE_MODULES = {
    "runsignup": runsignup,
    "hyrox": hyrox,
    "indiarunning": indiarunning,
}


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _handle_structured(site: dict, fetcher: Fetcher, log: Callable[[str], None]) -> List[Event]:
    """Listing pages, read through the fallback chain."""
    events: List[Event] = []
    for seed in site.get("seeds") or []:
        result = fetcher.get(seed, delay=site.get("delay_seconds"))
        if not result.ok:
            log(f"    {seed}: {result.error or result.status}")
            continue
        found, how = extract_page(result.text, site, seed)
        log(f"    {seed}: {len(found)}{how}")
        events.extend(found)
    return events


def extract_page(html: str, site: dict, seed: str) -> tuple:
    """Try each extraction strategy in turn, stopping at the first that yields events.

    schema.org first because it is an agreed vocabulary and therefore the most
    trustworthy; embedded JavaScript state next, since it is the site's own data
    rather than a guess; CSS selectors last, as they break whenever the markup
    is restyled. Returns (events, note) where the note names the strategy used.
    """
    nodes = jsonld.extract(html)
    found = [e for e in (schemaorg.to_event(n, site["key"], seed) for n in nodes) if e]
    if found:
        return found, ""

    if site.get("use_js", True):
        payloads = jsvars.extract(html)
        found, counts = generic.from_payloads(
            payloads, site["key"], seed, site.get("url_template")
        )
        if found:
            patterns = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            return found, f" (js: {patterns})"

    if site.get("selectors"):
        rows = css_extractor.extract(html, site["selectors"])
        found = [e for e in (_event_from_row(r, site, seed) for r in rows) if e]
        if found:
            return found, " (css fallback)"

    return [], ""


def _event_from_row(row: dict, site: dict, seed: str) -> Optional[Event]:
    """Map a CSS-fallback row onto an Event."""
    title = nz.clean_text(row.get("title"))
    source_url = nz.absolute_url(row.get("url"), seed)
    if not title or not source_url:
        return None
    labels, kms = nz.parse_distances([row.get("distance")])
    price = nz.parse_price(row.get("price"))
    return Event(
        title=title,
        source=site["key"],
        source_url=source_url,
        start=nz.parse_dt(row.get("date")),
        sport=nz.classify_sport(row.get("distance"), title),
        distances=labels,
        distance_km=kms,
        venue_name=nz.clean_text(row.get("venue")),
        price_min=price,
        price_max=price,
        ticket_url=source_url,
        found_on_url=seed,
        image_url=nz.absolute_url(row.get("image"), seed),
        scraped_at=nz.now_iso(),
    )


def _handle_module(site: dict, fetcher: Fetcher, log: Callable[[str], None]) -> List[Event]:
    module = SOURCE_MODULES.get(site["key"])
    if module is None:
        raise KeyError(f"no source module registered for '{site['key']}'")
    return module.collect(site, fetcher, log)


HANDLERS = {
    "jsonld": _handle_structured,
    "css": _handle_structured,
    "api": _handle_module,
    "nextdata": _handle_module,
}


def _probe(site: dict, fetcher: Fetcher) -> tuple:
    """Fetch a presumed-JS-only site once and try to read it anyway.

    Some sites labelled `requires_js` still ship their data in an inline script;
    the embedded-JSON extractor can reach that without a browser. Probing keeps
    the label honest - it is downgraded to a real result the moment one works,
    and otherwise records the HTTP status so a block is distinguishable from a
    deliberate skip. Returns (http_status, events, note, error).

    `error` matters: if the probe never got the page, saying "needs a browser"
    would be asserting something this run did not actually check.
    """
    seeds = site.get("seeds") or []
    if not seeds:
        return None, [], "", "no seeds configured"
    result = fetcher.get(seeds[0], delay=site.get("delay_seconds"))
    if not result.ok:
        return result.status, [], "", result.error or f"HTTP {result.status}"
    events, how = extract_page(result.text, site, seeds[0])
    return result.status, events, how, None


def crawl_site(site: dict, fetcher: Fetcher, log: Callable[[str], None]) -> tuple:
    """Run one site. Returns (SiteResult, events) - never raises."""
    key = site.get("key", "?")
    result = SiteResult(key=key, name=site.get("name", key), requires_js=bool(site.get("requires_js")))

    if not site.get("enabled", True):
        result.status = STATUS_SKIPPED
        result.reason = site.get("note") or "disabled in config"
        return result, []

    if site.get("requires_js"):
        result.http_status, events, how, error = _probe(site, fetcher)
        if events:
            # The label was pessimistic - the data was in the page after all.
            result.status = STATUS_OK
            result.events = len(events)
            result.reason = f"read without a browser{how}"
            log(f"    {result.events} events recovered from embedded JSON{how}")
            return result, events
        result.status = STATUS_BLOCKED
        if error:
            # Unreached, so the configured note is a claim we cannot stand behind.
            result.errors.append(error)
            result.reason = "not reached this run, so client-side rendering unconfirmed"
        else:
            result.reason = site.get("note") or "content rendered client-side"
        return result, []

    handler = HANDLERS.get(site.get("type", "jsonld"))
    if handler is None:
        result.status = STATUS_ERROR
        result.reason = f"unknown type '{site.get('type')}'"
        return result, []

    try:
        events = handler(site, fetcher, log)
    except Exception as exc:  # a broken source must not sink the whole run
        result.status = STATUS_ERROR
        result.errors.append(f"{type(exc).__name__}: {exc}")
        return result, []

    result.events = len(events)
    if not events:
        result.status = STATUS_EMPTY
        result.reason = result.reason or "no events parsed"
    return result, events


def _has_finished(event: Event) -> bool:
    """True only when we can show the event is over.

    Listing sites keep showing a race's last edition until the next one has a
    date (racesindia does), and a finished race is not something a user can
    enter. An event with no date at all - HYROX, whose dates are rendered
    client-side - is not provably finished, so it stays.
    """
    today = nz.now_iso()[:10]
    end = (event.end or "")[:10]
    if end:
        return end < today
    start = (event.start or "")[:10]
    return bool(start) and start < today


def _sort_key(event: Event):
    """Soonest first; events with no date (HYROX) sort to the end."""
    return (event.start is None, event.start or "", event.title.lower())


def crawl(
    config_path: Path,
    only: Optional[List[str]] = None,
    use_cache: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> RunReport:
    log = log or (lambda msg: print(msg))
    config = load_config(config_path)
    defaults = config.get("defaults") or {}

    fetcher = Fetcher(
        timeout=defaults.get("timeout", 25),
        default_delay=defaults.get("delay_seconds", 2.0),
        respect_robots=defaults.get("respect_robots", True),
        cache_dir=ROOT / ".cache",
        use_cache=use_cache,
        retries=defaults.get("retries", 2),
    )

    report = RunReport()
    collected: List[Event] = []

    for site in config.get("sites") or []:
        if only and site.get("key") not in only:
            continue
        log(f"\n[{site.get('key')}] {site.get('name', '')}")
        site = {**defaults, **site}
        site_result, events = crawl_site(site, fetcher, log)
        report.add(site_result)
        # Only fills a gap the source left; never overwrites what it stated.
        fallback_country = site.get("default_country")
        if fallback_country:
            for event in events:
                event.country = event.country or fallback_country
        collected.extend(events)
        if site_result.status == STATUS_BLOCKED:
            log(f"    blocked - {site_result.reason}")
            if site_result.errors:
                log(f"      {site_result.errors[0]}")
        elif site_result.errors:
            log(f"    error - {site_result.errors[0]}")

    for event in collected:
        event.country = nz.country_code(event.country)

    # An event without a URL has no stable identity and nowhere for the user to
    # go, so it is dropped rather than shipped half-formed.
    with_url = [e for e in collected if e.source_url]
    report.rejected_no_url = len(collected) - len(with_url)

    if defaults.get("sports_only", True):
        sporting = [e for e in with_url if e.sport != "other"]
        report.rejected_non_sport = len(with_url) - len(sporting)
        with_url = sporting

    if defaults.get("drop_past", True):
        upcoming = [e for e in with_url if not _has_finished(e)]
        report.rejected_past = len(with_url) - len(upcoming)
        with_url = upcoming

    kept, dropped = nz.dedupe(with_url)

    # A partial run refreshes the sources it was asked for; events from the
    # other sources are carried over rather than silently discarded.
    if only:
        carried = [e for e in load_events() if e.get("source") not in set(only)]
        # Carried rows were filtered when they were written, but "already
        # finished" is a claim about today, so it has to be re-checked.
        if defaults.get("drop_past", True):
            fresh = [e for e in carried if not _has_finished(Event(**e))]
            report.rejected_past += len(carried) - len(fresh)
            carried = fresh
        for key in sorted({e.get("source") for e in carried}):
            report.add(
                SiteResult(
                    key=key,
                    name=key,
                    status=STATUS_CARRIED,
                    events=sum(1 for e in carried if e.get("source") == key),
                    reason="not in --only; kept from previous run",
                )
            )
        kept = [Event(**e) for e in carried] + kept

    kept.sort(key=_sort_key)

    # After the --only merge, so carried rows from an older run get located too.
    if defaults.get("geocode", True):
        geocode.annotate(kept, Geocoder(cache_path=GEOCODE_CACHE), log)
    report.geocoded = sum(1 for e in kept if e.coords_source == COORDS_GEOCODED)
    report.without_coords = sum(1 for e in kept if e.latitude is None or e.longitude is None)

    report.duplicates_dropped = dropped
    report.total_events = len(kept)
    report.requests_made = fetcher.request_count
    report.finished_at = nz.now_iso()

    write_output(kept, report)
    return report


def write_output(events: List[Event], report: RunReport) -> None:
    save_events(events)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2, ensure_ascii=False)


def save_events(events: List[Event]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_PATH, "w", encoding="utf-8") as handle:
        json.dump([e.to_dict() for e in events], handle, indent=2, ensure_ascii=False)


def geocode_existing(log: Optional[Callable[[str], None]] = None) -> Tuple[int, int]:
    """Locate the events already in output/events.json without re-crawling."""
    log = log or (lambda msg: print(msg))
    events = [Event(**e) for e in load_events()]
    geocoded, missing = geocode.annotate(events, Geocoder(cache_path=GEOCODE_CACHE), log)
    save_events(events)
    report = load_report()
    if report:
        report["geocoded"] = sum(1 for e in events if e.coords_source == COORDS_GEOCODED)
        report["without_coords"] = missing
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return geocoded, missing


def load_events() -> List[dict]:
    if not EVENTS_PATH.exists():
        return []
    try:
        with open(EVENTS_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []


def load_report() -> dict:
    if not REPORT_PATH.exists():
        return {}
    try:
        with open(REPORT_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
