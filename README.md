# EventCrawler — Phase 1

Crawls endurance-sport listing sites (marathons, trail races, triathlons,
duathlons, cycling, swimming, HYROX), normalises every result into one JSON
schema, and serves a local page to browse and download it.

## Quick start

```bash
pip install -r requirements.txt
python run.py crawl          # writes output/events.json + output/run_report.json
python run.py serve          # http://127.0.0.1:5000
```

## What it crawls

| Source | How it is read | Notes |
|---|---|---|
| RunSignup | public REST API | largest source; US-heavy, 7 event types |
| IndiaRunning | `__NEXT_DATA__` + sitemap.xml | detail pages carry coordinates and per-category pricing |
| AllEvents.in | schema.org JSON-LD | eight Indian city sports pages; robots asks for a 10s delay |
| Townscript | schema.org JSON-LD | seven Indian sport categories; a prerender service serves full HTML to crawler user agents |
| RacesIndia | embedded JS state | Next.js flight data; India-only, strong on trail and ultra races |
| HYROX | WordPress REST API | race cities only — see *Known gaps* |
| IRONMAN | — | **blocked**, see *Known gaps* |

`--only KEY` refreshes just those sources and carries the rest forward from the
previous run, so a partial crawl never wipes the dataset.

Sites live in `sites.yaml`. Add one by giving it a `key`, a `type`
(`api`, `jsonld`, `nextdata`, `css`) and its seeds; `api`/`nextdata` types also
need a matching module under `eventcrawler/sources/`.

## How a page is read

Every HTML page goes through the same three steps, stopping at the first one
that returns events:

1. **schema.org JSON-LD** — `<script type="application/ld+json">`. Tried first
   because it is an agreed vocabulary, so the fields mean what they say.
2. **Embedded JavaScript state** — the site's own data, left in the page for its
   front end to hydrate from: `__NEXT_DATA__`, `__NUXT__`, `__INITIAL_STATE__`,
   `__PRELOADED_STATE__`, `__APOLLO_STATE__`, `__remixContext`, Next.js App
   Router flight data (`self.__next_f.push([1,"…"])`), plain
   `var x = {…}` assignments and `JSON.parse("…")` arguments.
3. **CSS selectors** — the `selectors:` block in `sites.yaml`. Last, because
   selectors break the moment the markup is restyled.

Step 2 executes nothing. `extractors/jsvars.py` scans inline scripts with a
string-aware bracket matcher to lift out JSON literals; `sources/generic.py`
then decides which of the resulting objects are events, by matching their keys
against aliases (`startDate`/`start_date`/`event_date`, `venue`/`location`, …).
An object has to carry a plausible title **plus a date, URL or slug, plus at
least two supporting fields** before it is accepted, so navigation entries,
config blobs and banner carousels are rejected. Anything that still has no
resolvable link is dropped.

Two per-site options tune this: `use_js: false` skips step 2 for a site whose
inline JSON is noisy, and `url_template: "https://…/{slug}"` turns a slug found
in inline JSON into a real link. A third, `default_country:`, fills in a country
the markup omits — only where the source left the field empty, never over a
value it stated.

The run report names the step each source was read by, e.g.
`(js: __NUXT__=42)`.

Sites marked `requires_js` are fetched and run through the chain anyway. If
their data turns out to be sitting in an inline script they are reported as
`ok`; only when nothing is found are they reported as `blocked`.

`python tests/test_js_extraction.py` exercises step 2 against fixtures covering
all five patterns, plus the rejection cases. It needs no network.

## Output

`output/events.json` is a plain JSON array — one object per event, safe to pipe
straight into anything else:

```json
{
  "id": "3f1c9a2b7d4e5061",
  "title": "Tata Mumbai Marathon",
  "source": "indiarunning",
  "source_url": "https://www.indiarunning.com/events/tata-mumbai-marathon",
  "start": "2027-01-17T05:00:00+05:30",
  "date_status": "ok",
  "sport": "running",
  "distances": ["5K", "10K", "Half Marathon"],
  "distance_km": [5.0, 10.0, 21.0975],
  "city": "Mumbai",
  "country": "India",
  "latitude": 18.9334056,
  "price_min": 800.0,
  "price_max": 2200.0,
  "currency": "INR",
  "ticket_url": "https://www.indiarunning.com/events/tata-mumbai-marathon",
  "found_on_url": "https://www.indiarunning.com/events"
}
```

Every record keeps two links: `source_url` (the event's own page) and
`found_on_url` (the listing it was discovered on). `ticket_url` is the
registration link where the source gives one.

Two `defaults:` in `sites.yaml` filter the result set: `sports_only` drops
records that classify as sport `other`, and `drop_past` drops events that have
already finished. The latter matters because listing sites keep showing last
year's edition until the next one has a date, and it is re-applied to events
carried over by `--only`, since "already finished" is a claim about today rather
than about the run that first wrote the record. An event with no date at all
(HYROX) is not provably finished and is kept.

`output/run_report.json` records what each source returned, so a zero is never
ambiguous — it says whether the site was blocked, empty, or errored, and with
which HTTP status.

## Viewer

`python run.py serve` gives a table filtered by sport, source and free text,
with **Download JSON**, a raw-JSON view, the run report, and a **Re-crawl**
button. It binds to `127.0.0.1` only.

## Known gaps (deliberate, not bugs)

- **IRONMAN** builds its race list through Drupal `views_ajax`; there is no
  JSON-LD, no inline JSON and no public API. Phase 1 fetches the page once, runs
  the full extraction chain over it, and reports `blocked` when that finds
  nothing. It needs a headless browser — phase 2. (Rendering pages in a browser
  is the one thing phase 1 does not do; everything else about "JavaScript sites"
  is covered by step 2 above.)
- **HYROX dates** are rendered client-side. Rather than pass the WordPress post
  date off as a race date, those events carry `start: null` and
  `date_status: "unavailable_phase1"`. The viewer shows them as *date TBC* and
  sorts them last.
- Cloudflare-protected aggregators (ahotu, letsdothis, findarace, trifind) return
  403 to any plain HTTP client and are not in `sites.yaml`.

## Politeness

`robots.txt` is checked before every request and `Crawl-delay` is honoured on
top of the configured per-host delay. Failed fetches are returned, not raised,
so one blocked site never sinks a run. `--cache` reuses already-downloaded pages
while you iterate on parsing.

## Layout

```
run.py                     CLI: crawl / serve
sites.yaml                 sources and per-site settings
eventcrawler/
  crawler.py               orchestration, dedupe, output
  fetcher.py               robots-aware HTTP with per-host throttling
  models.py                the Event record every source maps onto
  normalize.py             dates, prices, addresses, distances, sport, dedupe
  report.py                per-run audit trail
  extractors/              jsonld, jsvars, nextdata, css
  sources/                 schemaorg, generic, runsignup, indiarunning, hyrox
  web/                     Flask viewer
tests/
  test_js_extraction.py    fixtures for the embedded-JS path (no network)
tools/
  fetch_seeds.py           cache candidate URLs under .cache/probe
  analyze_seeds.py         verdict per candidate: which step, if any, reads it
  seed_candidates*.txt     the URLs surveyed; seed_verdicts.json is the result
output/                    events.json + run_report.json (gitignored)
```

## Vetting a new seed site

```bash
python tools/fetch_seeds.py      # cache the pages listed in tools/seed_candidates*.txt
python tools/analyze_seeds.py    # -> tools/seed_verdicts.json
```

Each URL gets one of: `html (JSON-LD)`, `html (CSS only)`, `js present, no
events` (an inline payload exists but nothing in it looks like an event),
`no data in HTML` (needs a browser), or `unreachable`. The verdict is a floor —
it reads every page with one generic config, so a site whose records carry a
slug rather than a URL scores zero until `url_template` is set for it.
