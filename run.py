#!/usr/bin/env python
"""EventCrawler CLI.

    python run.py crawl                 crawl every enabled site
    python run.py crawl --only hyrox    crawl one site
    python run.py crawl --cache         reuse pages already downloaded
    python run.py geocode               locate existing events without re-crawling
    python run.py serve                 browse and download the JSON
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eventcrawler.crawler import EVENTS_PATH, REPORT_PATH, crawl

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "sites.yaml"


def cmd_crawl(args: argparse.Namespace) -> int:
    config = Path(args.config)
    if not config.exists():
        print(f"config not found: {config}", file=sys.stderr)
        return 1

    report = crawl(config_path=config, only=args.only, use_cache=args.cache)
    print(report.console_summary())
    print(f"\n  events -> {EVENTS_PATH}")
    print(f"  report -> {REPORT_PATH}")
    if report.blocked:
        names = ", ".join(s.key for s in report.blocked)
        print(f"\n  note: {names} need a headless browser and were skipped (phase 2)")
    print("\n  run `python run.py serve` to browse and download the JSON")
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    from eventcrawler.crawler import geocode_existing

    if not EVENTS_PATH.exists():
        print("no events yet - run `python run.py crawl` first", file=sys.stderr)
        return 1
    geocode_existing()
    print(f"\n  events -> {EVENTS_PATH}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from eventcrawler.web.app import create_app

    app = create_app(config_path=Path(args.config))
    print(f"  viewer on http://127.0.0.1:{args.port}  (ctrl-c to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description="EventCrawler phase 1")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl_parser = sub.add_parser("crawl", help="fetch events and write output/events.json")
    crawl_parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    crawl_parser.add_argument("--only", nargs="+", metavar="KEY", help="site keys to crawl")
    crawl_parser.add_argument("--cache", action="store_true", help="reuse cached pages")
    crawl_parser.set_defaults(func=cmd_crawl)

    geocode_parser = sub.add_parser(
        "geocode", help="add coordinates to the existing output/events.json without re-crawling"
    )
    geocode_parser.set_defaults(func=cmd_geocode)

    serve_parser = sub.add_parser("serve", help="open the local viewer")
    serve_parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    serve_parser.add_argument("--port", type=int, default=5000)
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
