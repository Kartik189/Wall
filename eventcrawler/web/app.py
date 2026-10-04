"""Local viewer for the crawled events.

Deliberately small: one page that reads output/events.json, plus a download
endpoint and a button to re-run the crawl. Bound to 127.0.0.1 only.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request, send_file

from ..crawler import EVENTS_PATH, GEOCODE_CACHE, REPORT_PATH, crawl, load_events, load_report
from ..geocode import Geocoder

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = ROOT / "sites.yaml"

#: Guards against a second crawl being kicked off while one is in flight.
_crawl_lock = threading.Lock()


def _summary(events: list) -> dict:
    sports: dict = {}
    sources: dict = {}
    dateless = 0
    located = 0
    for event in events:
        sports[event.get("sport", "other")] = sports.get(event.get("sport", "other"), 0) + 1
        sources[event.get("source", "?")] = sources.get(event.get("source", "?"), 0) + 1
        if not event.get("start"):
            dateless += 1
        if event.get("latitude") is not None and event.get("longitude") is not None:
            located += 1
    return {
        "total": len(events),
        "sports": sorted(sports.items(), key=lambda kv: (-kv[1], kv[0])),
        "sources": sorted(sources.items(), key=lambda kv: (-kv[1], kv[0])),
        "dateless": dateless,
        "unlocated": len(events) - located,
        "geocoded": sum(1 for e in events if e.get("coords_source") == "geocoded"),
    }


def create_app(config_path: Optional[Path] = None) -> Flask:
    app = Flask(__name__)
    app.config["CONFIG_PATH"] = Path(config_path or DEFAULT_CONFIG)
    # One instance so its throttle and cache are shared across requests.
    geocoder = Geocoder(cache_path=GEOCODE_CACHE)

    @app.get("/")
    def index():
        events = load_events()
        report = load_report()
        blocked = [s for s in report.get("sites", []) if s.get("status") == "blocked"]
        return render_template(
            "index.html",
            events=events,
            summary=_summary(events),
            report=report,
            blocked=blocked,
            has_output=EVENTS_PATH.exists(),
        )

    @app.get("/events.json")
    def events_json():
        return jsonify(load_events())

    @app.get("/download")
    def download():
        if not EVENTS_PATH.exists():
            return jsonify({"error": "no events yet - run a crawl first"}), 404
        return send_file(EVENTS_PATH, as_attachment=True, download_name="events.json")

    @app.get("/report.json")
    def report_json():
        return jsonify(load_report())

    @app.get("/download/report")
    def download_report():
        if not REPORT_PATH.exists():
            return jsonify({"error": "no report yet - run a crawl first"}), 404
        return send_file(REPORT_PATH, as_attachment=True, download_name="run_report.json")

    @app.get("/geocode")
    def geocode_place():
        """Resolve a typed place ("Koramangala, Bengaluru") to coordinates for
        the near-me filter, for when the browser will not share its location."""
        query = (request.args.get("q") or "").strip()[:200]
        if not query:
            return jsonify({"error": "missing ?q="}), 400
        hit = geocoder.lookup(query)
        if hit:
            geocoder.save()
            return jsonify(hit)
        if geocoder.last_error:
            return jsonify({"error": f"geocoding failed - {geocoder.last_error}"}), 503
        geocoder.save()
        return jsonify({"error": f"could not find '{query}'"}), 404

    @app.post("/crawl")
    def recrawl():
        if not _crawl_lock.acquire(blocking=False):
            return jsonify({"status": "busy", "message": "a crawl is already running"}), 409
        try:
            report = crawl(config_path=app.config["CONFIG_PATH"], log=lambda msg: None)
            return jsonify({"status": "ok", "total": report.total_events})
        except Exception as exc:
            return jsonify({"status": "error", "message": f"{type(exc).__name__}: {exc}"}), 500
        finally:
            _crawl_lock.release()

    return app
