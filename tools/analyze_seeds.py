"""Verdict per candidate URL: which extraction step, if any, reads this page.

Run `fetch_seeds.py` first - it caches each page under .cache/probe using the
crawler's own user agent, which matters: Townscript's prerender service serves a
5 KB empty shell to a browser UA and full HTML with Event markup to a crawler.

The verdict is a floor, not a ceiling. This reads every page with one generic
config, so a site whose records carry a slug rather than a URL scores zero here
and still works once `url_template` is set for it in sites.yaml - racesindia is
exactly that case.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eventcrawler.extractors import css as css_extractor
from eventcrawler.extractors import jsonld, jsvars
from eventcrawler.sources import generic, schemaorg

CACHE = ROOT / ".cache/probe"
SELECTORS = {
    "container": "article, .event, .event-card, .race, .card, li.event, .event-item, .tribe-events-calendar-list__event",
    "title": "h1, h2, h3, .title, .event-title, a",
    "url": "a",
    "date": ".date, time, .event-date",
    "venue": ".venue, .location, .city",
}


def analyze(url):
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    idx = json.load(open(CACHE / "index.json"))
    if not (path.exists() and path.stat().st_size):
        return {"url": url, "verdict": "unreachable", "detail": idx.get(url, "?")}

    html = path.read_text(encoding="utf-8")
    out = {"url": url, "bytes": len(html)}

    nodes = jsonld.extract(html)
    jld = [e for e in (schemaorg.to_event(n, "probe", url) for n in nodes) if e]
    out["jsonld"] = len(jld)
    out["jsonld_titles"] = [e.title for e in jld[:4]]

    payloads = jsvars.extract(html)
    out["payload_labels"] = sorted({l for l, _ in payloads})
    js, counts = generic.from_payloads(payloads, "probe", url)
    out["js"] = len(js)
    out["js_titles"] = [e.title for e in js[:6]]

    try:
        rows = [r for r in css_extractor.extract(html, SELECTORS) if r.get("title") and r.get("url")]
    except Exception:
        rows = []
    out["css"] = len(rows)
    out["css_titles"] = [str(r.get("title"))[:50] for r in rows[:4]]

    if jld:
        out["verdict"] = "html (JSON-LD)"
    elif js:
        out["verdict"] = "js (inline JSON)"
    elif rows:
        out["verdict"] = "html (CSS only)"
    elif payloads:
        out["verdict"] = "js present, no events"
    else:
        out["verdict"] = "no data in HTML"
    return out


if __name__ == "__main__":
    urls = []
    for f in (ROOT / "tools/seed_candidates.txt", ROOT / "tools/seed_candidates2.txt"):
        urls += [l.strip() for l in open(f, encoding="utf-8") if l.strip()]
    urls = list(dict.fromkeys(urls))
    rows = [analyze(u) for u in urls]
    json.dump(rows, open(ROOT / "tools/seed_verdicts.json", "w", encoding="utf-8"), indent=2, default=str)
    print(f"analyzed {len(rows)} urls -> tools/seed_verdicts.json")
