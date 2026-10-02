"""Pull JSON payloads out of inline <script> blocks.

Many sites server-render their data into a JavaScript variable rather than into
schema.org markup - `window.__NUXT__`, `__INITIAL_STATE__`, Next.js flight
chunks, and so on. This extractor finds those blobs and parses them, so the
crawler can fall back to them when a page carries no JSON-LD.

It reads only what the server already sent; it does not execute JavaScript.
Anything that is not valid JSON once isolated is skipped rather than guessed at.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, List, Optional, Tuple

BACKSLASH = chr(92)

#: Conventional names SSR frameworks hang their state off.
KNOWN_GLOBALS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__PRELOADED_STATE__",
    "__APOLLO_STATE__",
    "__remixContext",
    "__INITIAL_DATA__",
    "__SERVER_DATA__",
    "__APP_STATE__",
    "__STATE__",
    "__DATA__",
    "__sveltekit_data",
)

_GLOBAL_RE = re.compile(
    r"(?:window|self|globalThis)\s*(?:\.\s*(" + "|".join(KNOWN_GLOBALS) + r")"
    r"|\[\s*[\"'](" + "|".join(KNOWN_GLOBALS) + r")[\"']\s*\])\s*=\s*",
)
_BARE_GLOBAL_RE = re.compile(r"\b(" + "|".join(KNOWN_GLOBALS) + r")\s*=\s*")
_ASSIGN_RE = re.compile(
    r"(?:\b(?:var|let|const)\s+|(?:window|self|globalThis)\s*\.\s*)([A-Za-z_$][\w$]*)\s*=\s*(?=[\{\[])"
)
_FLIGHT_RE = re.compile(r"__next_f\s*\.\s*push\(\s*\[\s*\d+\s*,\s*(?=[\"'])")
_JSON_PARSE_RE = re.compile(r"JSON\s*\.\s*parse\s*\(\s*(?=[\"'])")
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_TYPE_RE = re.compile(r"""type\s*=\s*["']?([\w/+.-]+)""", re.I)

#: A named global (`window.__NUXT__`) is worth parsing at almost any size; an
#: anonymous `var x = {...}` needs to be big enough to plausibly be data, or
#: every feature flag on the page gets walked.
MIN_BLOB_CHARS = 40
MIN_ANON_BLOB_CHARS = 200
#: Ceiling on how many blobs one page may contribute, so a pathological page
#: cannot stall a run.
MAX_BLOBS = 400


def _scan_literal(text: str, start: int) -> Optional[str]:
    """Return the balanced JSON literal starting at `text[start]`.

    Tracks string state so braces inside string values do not confuse the
    depth count. Valid JSON nests properly, so counting only the outermost
    bracket type is enough.
    """
    opener = text[start : start + 1]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == BACKSLASH:
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _scan_js_string(text: str, start: int) -> Optional[str]:
    """Return the JavaScript string literal starting at `text[start]`, quotes included."""
    quote = text[start : start + 1]
    if quote not in ("'", '"'):
        return None
    escaped = False
    for i in range(start + 1, len(text)):
        char = text[i]
        if escaped:
            escaped = False
        elif char == BACKSLASH:
            escaped = True
        elif char == quote:
            return text[start : i + 1]
    return None


def _loads(raw: Optional[str], min_chars: int = MIN_BLOB_CHARS) -> Any:
    if not raw or len(raw) < min_chars:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None


def _script_bodies(html: str) -> Iterator[Tuple[str, str]]:
    """Yield (type_attribute, body) for every <script> in the page."""
    for match in _SCRIPT_RE.finditer(html):
        attrs, body = match.group(1), match.group(2)
        type_match = _TYPE_RE.search(attrs or "")
        yield (type_match.group(1).lower() if type_match else ""), body


def _from_json_scripts(html: str) -> Iterator[Tuple[str, Any]]:
    """<script type="application/json"> tags, including Next.js's __NEXT_DATA__.

    JSON-LD is skipped - the dedicated extractor already owns it.
    """
    for script_type, body in _script_bodies(html):
        if script_type in ("application/json", "application/x-json") or (
            script_type == "application/json+ld"
        ):
            if "ld+json" in script_type:
                continue
            parsed = _loads(body.strip())
            if parsed is not None:
                yield "script[type=json]", parsed


def _from_globals(body: str) -> Iterator[Tuple[str, Any]]:
    """`window.__NUXT__ = {...}` and friends, plus the bare `__NUXT__ = {...}` form."""
    seen_at = set()
    for regex in (_GLOBAL_RE, _BARE_GLOBAL_RE):
        for match in regex.finditer(body):
            start = match.end()
            if start in seen_at:
                continue
            seen_at.add(start)
            name = next((g for g in match.groups() if g), "global")
            parsed = _loads(_scan_literal(body, start))
            if parsed is not None:
                yield name, parsed


def _from_flight(body: str) -> Iterator[Tuple[str, Any]]:
    """Next.js App Router streams its payload as `self.__next_f.push([1, "chunk"])`.

    The chunks concatenate into one document of `id:<json>` lines; each line's
    value is parsed independently so one malformed row cannot lose the rest.
    """
    chunks: List[str] = []
    for match in _FLIGHT_RE.finditer(body):
        literal = _scan_js_string(body, match.end())
        if not literal:
            continue
        try:
            # strict=False: streamed chunks occasionally carry raw control
            # characters that a strict JSON string literal would reject.
            chunks.append(json.loads(literal, strict=False))
        except (json.JSONDecodeError, ValueError):
            continue
    if not chunks:
        return

    payload = "".join(chunks)
    for line in payload.split("\n"):
        _, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.lstrip()
        # Flight rows are prefixed with a type sigil (I, T, HL...) before JSON.
        offset = next((i for i, c in enumerate(value) if c in "{["), None)
        if offset is None or offset > 2:
            continue
        parsed = _loads(_scan_literal(value, offset))
        if parsed is not None:
            yield "__next_f", parsed


def _from_assignments(body: str) -> Iterator[Tuple[str, Any]]:
    """Any other `var x = {...}` / `window.x = [...]` large enough to be data."""
    for match in _ASSIGN_RE.finditer(body):
        parsed = _loads(_scan_literal(body, match.end()), MIN_ANON_BLOB_CHARS)
        if parsed is not None:
            yield match.group(1), parsed


def _from_json_parse(body: str) -> Iterator[Tuple[str, Any]]:
    """`JSON.parse("{...}")` - the payload is a JS string wrapping JSON."""
    for match in _JSON_PARSE_RE.finditer(body):
        literal = _scan_js_string(body, match.end())
        if not literal:
            continue
        try:
            inner = json.loads(
                literal if literal.startswith('"') else '"' + literal[1:-1] + '"', strict=False
            )
        except (json.JSONDecodeError, ValueError):
            continue
        parsed = _loads(inner)
        if parsed is not None:
            yield "JSON.parse", parsed


def extract(html: str) -> List[Tuple[str, Any]]:
    """Return every JSON payload embedded in the page's scripts.

    Results are (label, data) pairs; the label says which pattern found it and
    is carried into logs so an odd result can be traced back to its source.
    Identical payloads found by more than one pattern are returned once.
    """
    if not html:
        return []

    found: List[Tuple[str, Any]] = []
    seen: set = set()

    def offer(label: str, data: Any) -> None:
        if len(found) >= MAX_BLOBS:
            return
        try:
            fingerprint = hash(json.dumps(data, sort_keys=True, default=str))
        except (TypeError, ValueError, RecursionError):
            return
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        found.append((label, data))

    for label, data in _from_json_scripts(html):
        offer(label, data)

    for script_type, body in _script_bodies(html):
        if "ld+json" in script_type or "json" in script_type:
            continue  # already handled, or owned by the JSON-LD extractor
        for producer in (_from_globals, _from_flight, _from_assignments, _from_json_parse):
            for label, data in producer(body):
                offer(label, data)
            if len(found) >= MAX_BLOBS:
                break

    return found
