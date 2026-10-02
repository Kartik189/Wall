"""Turn messy per-source values into the canonical fields on :class:`Event`.

Every helper here is total: it returns ``None`` rather than raising, because one
malformed field on one event must never abort a crawl.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from dateutil import parser as dateparser

MILE_KM = 1.609344

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# shape coercion
# --------------------------------------------------------------------------- #
def coerce_list(value) -> list:
    """schema.org fields are routinely either a scalar or a list of them."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


def first(value):
    items = coerce_list(value)
    return items[0] if items else None


def clean_text(value, limit: Optional[int] = None) -> Optional[str]:
    """Strip tags, unescape entities, collapse whitespace. Live data has both
    embedded HTML (RunSignup descriptions) and raw entities (AllEvents venues)."""
    if value is None:
        return None
    text = _TAG_RE.sub(" ", str(value))
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "..."
    return text


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
def parse_dt(value, dayfirst: bool = False) -> Optional[str]:
    """Accepts every format observed live: 2026-10-11, 2026-09-27T08:00:00.000Z,
    10/10/2026 and 10/10/2026 09:00 (RunSignup is US month-first)."""
    if value is None:
        return None
    text = clean_text(value)
    if not text or text.lower() in ("null", "none", "tbd", "tba"):
        return None
    try:
        parsed = dateparser.parse(text, dayfirst=dayfirst)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.isoformat() if parsed else None


# --------------------------------------------------------------------------- #
# money
# --------------------------------------------------------------------------- #
def parse_price(value) -> Optional[float]:
    """Money-ish string to float. Also used for lat/lon strings."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def parse_offers(offers, base_url: Optional[str] = None):
    """schema.org offers -> (price_min, price_max, currency, ticket_url).

    Handles both shapes seen live: AggregateOffer with lowPrice/highPrice on
    AllEvents, and a single Offer with price on District.
    """
    low = high = None
    currency = None
    url = None
    for offer in coerce_list(offers):
        if not isinstance(offer, dict):
            continue
        currency = currency or clean_text(offer.get("priceCurrency"))
        url = url or absolute_url(offer.get("url"), base_url)
        values = []
        for key in ("lowPrice", "price", "minPrice", "highPrice", "maxPrice"):
            price = parse_price(offer.get(key))
            if price is not None:
                values.append(price)
        if values:
            low = min(values) if low is None else min(low, min(values))
            high = max(values) if high is None else max(high, max(values))
    return low, high, currency, url


def price_range(values: Iterable) -> Tuple[Optional[float], Optional[float]]:
    prices = [p for p in (parse_price(v) for v in values) if p is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)


# --------------------------------------------------------------------------- #
# places
# --------------------------------------------------------------------------- #
_STREET_KEYS = ("streetAddress", "street", "line1", "address1", "addressLine1")
_CITY_KEYS = ("addressLocality", "city", "area")
_REGION_KEYS = ("addressRegion", "state", "region")
_COUNTRY_KEYS = ("addressCountry", "country", "country_code")
_POSTAL_KEYS = ("postalCode", "zipcode", "pinCode", "postal_code", "zip")


def _pick(data: dict, keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        text = clean_text(value)
        if text:
            return text
    return None


def flatten_address(address):
    """-> (address, city, region, country). Works on schema.org PostalAddress,
    RunSignup address dicts and IndiaRunning location dicts alike."""
    if not isinstance(address, dict):
        return clean_text(address), None, None, None

    city = _pick(address, _CITY_KEYS)
    region = _pick(address, _REGION_KEYS)
    country = _pick(address, _COUNTRY_KEYS)
    street = _pick(address, _STREET_KEYS)
    postal = _pick(address, _POSTAL_KEYS)

    parts: List[str] = []
    for part in (street, city, region, postal, country):
        # Sources repeat the city inside the street line; do not echo it twice.
        if part and not any(part.lower() == seen.lower() for seen in parts):
            parts.append(part)
    return (", ".join(parts) or None), city, region, country


# --------------------------------------------------------------------------- #
# links
# --------------------------------------------------------------------------- #
def absolute_url(url, base: Optional[str] = None) -> Optional[str]:
    """Return an absolute http(s) URL, or None. source_url is load-bearing for
    dedupe, so callers treat None as a reason to reject the event."""
    text = clean_text(url)
    if not text:
        return None
    if base:
        text = urljoin(base, text)
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # Townscript emits "https://host//e/slug". Left alone, that same event
    # reached by a clean link would survive dedupe as a second record.
    if "//" in parsed.path:
        collapsed = re.sub(r"/{2,}", "/", parsed.path)
        text = parsed._replace(path=collapsed).geturl()
    return text


# --------------------------------------------------------------------------- #
# sport + distance
# --------------------------------------------------------------------------- #
# Exact source tokens (RunSignup event_type, IndiaRunning sportName) win over
# the fuzzier title matching below.
_SPORT_BY_TOKEN = {
    "running_race": "running",
    "running": "running",
    "trail_race": "trail_running",
    "trail running": "trail_running",
    "walking_only": "walking",
    "walking": "walking",
    "triathlon": "triathlon",
    "duathlon": "duathlon",
    "bike_race": "cycling",
    "cycling": "cycling",
    "swim": "swimming",
    "swimming": "swimming",
    "adventure_race": "adventure",
    "hyrox": "hyrox",
}

# Ordered: first match wins, so "Trail Marathon" is a trail race and
# "Ironman 70.3" is a triathlon rather than a run.
_SPORT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"hyrox", re.I), "hyrox"),
    (re.compile(r"triathlon|aquathlon|aquabike|ironman|\b70\.3\b|\b140\.6\b", re.I), "triathlon"),
    (re.compile(r"duathlon", re.I), "duathlon"),
    (re.compile(r"\btrail\b|\bultra\w*", re.I), "trail_running"),
    (re.compile(r"\bcycl\w*|\bbike\b|\bbiking\b|\bbrevet\b|gran fondo", re.I), "cycling"),
    (re.compile(r"\bswim\w*|open water", re.I), "swimming"),
    (re.compile(r"marathon|\bruns?\b|\brunning\b|\b\d+\s?k\b|\bhm\b", re.I), "running"),
    (re.compile(r"\bwalk\w*", re.I), "walking"),
]


def classify_sport(*hints) -> str:
    """Map source labels plus title onto the canonical sport enum."""
    values: List[str] = []
    for hint in hints:
        values.extend(str(v) for v in coerce_list(hint) if v not in (None, ""))

    for value in values:
        token = value.strip().lower()
        if token in _SPORT_BY_TOKEN:
            return _SPORT_BY_TOKEN[token]

    blob = " ".join(values)
    for pattern, sport in _SPORT_PATTERNS:
        if pattern.search(blob):
            return sport
    return "other"


# Distance shorthands used by IndiaRunning (HM, M) and race sites generally.
_DISTANCE_ALIASES = {
    "hm": 21.0975,
    "half": 21.0975,
    "half marathon": 21.0975,
    "m": 42.195,
    "fm": 42.195,
    "full": 42.195,
    "marathon": 42.195,
    "full marathon": 42.195,
}


def _label_for_km(km: Optional[float]) -> Optional[str]:
    if km is None:
        return None
    if abs(km - 42.195) <= 0.75:
        return "Marathon"
    if abs(km - 21.0975) <= 0.75:
        return "Half Marathon"
    for target in (5, 10, 15, 25, 50, 100):
        if abs(km - target) <= 0.35:
            return f"{target}K"
    if abs(km - 113.1) <= 2:
        return "Ironman 70.3"
    if abs(km - 226.3) <= 4:
        return "Ironman"
    if abs(km - round(km)) < 0.05:
        return f"{int(round(km))}K"
    return f"{km:.1f}K"


def parse_distance(raw) -> Tuple[Optional[str], Optional[float]]:
    """3.1 Miles -> (5K, 4.989); HM -> (Half Marathon, 21.098)."""
    text = clean_text(raw)
    if not text:
        return None, None

    key = text.strip().lower()
    if key in _DISTANCE_ALIASES:
        km = _DISTANCE_ALIASES[key]
        return _label_for_km(km), round(km, 3)

    match = _NUM_RE.search(key.replace(",", ""))
    if not match:
        return text, None
    number = float(match.group())

    # Branded triathlon distances are quoted in miles.
    if abs(number - 70.3) < 0.01 or abs(number - 140.6) < 0.01:
        km = number * MILE_KM
    elif "mile" in key or re.search(r"\bmi\b", key):
        km = number * MILE_KM
    elif "meter" in key or "metre" in key or (re.search(r"\d\s*m\b", key) and number >= 400):
        km = number / 1000.0
    else:
        km = number
    return _label_for_km(round(km, 3)), round(km, 3)


def parse_distances(values: Iterable) -> Tuple[List[str], List[float]]:
    """Order-preserving, de-duplicated distance labels plus kilometre values."""
    labels: List[str] = []
    kms: List[float] = []
    for value in coerce_list(list(values)):
        label, km = parse_distance(value)
        if label and label not in labels:
            labels.append(label)
        if km is not None and km not in kms:
            kms.append(km)
    return labels, sorted(kms)


# --------------------------------------------------------------------------- #
# dedupe
# --------------------------------------------------------------------------- #
_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Year/edition noise that stops the same race matching across two sources.
_TITLE_NOISE_RE = re.compile(r"\b(20\d{2}|\d{1,2}(st|nd|rd|th)\s+edition|edition)\b", re.I)


def title_key(title: Optional[str]) -> str:
    text = _TITLE_NOISE_RE.sub(" ", title or "")
    return _ALNUM_RE.sub(" ", text.lower()).strip()


def dedupe(events: Sequence) -> Tuple[list, int]:
    """Drop repeats by id, then by (title, start date). Returns (kept, dropped)."""
    seen_ids = set()
    seen_titles = set()
    kept = []
    dropped = 0
    for event in events:
        if event.id in seen_ids:
            dropped += 1
            continue
        key = (title_key(event.title), (event.start or "")[:10])
        if key[0] and key[1] and key in seen_titles:
            dropped += 1
            continue
        seen_ids.add(event.id)
        if key[0] and key[1]:
            seen_titles.add(key)
        kept.append(event)
    return kept, dropped


#: Sources disagree on country format - schema.org markup tends to give ISO-2,
#: APIs tend to give the full name. Everything is folded to ISO-2 so the field
#: is filterable; anything unrecognised is left exactly as the source gave it.
_COUNTRY_ISO2 = {
    "india": "IN", "united states": "US", "united states of america": "US",
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "america": "US",
    "united kingdom": "GB", "great britain": "GB", "uk": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "canada": "CA", "australia": "AU", "new zealand": "NZ", "ireland": "IE",
    "germany": "DE", "deutschland": "DE", "france": "FR", "spain": "ES",
    "espana": "ES", "italy": "IT", "italia": "IT", "portugal": "PT",
    "netherlands": "NL", "the netherlands": "NL", "belgium": "BE",
    "switzerland": "CH", "austria": "AT", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "poland": "PL", "czech republic": "CZ",
    "south africa": "ZA", "singapore": "SG", "malaysia": "MY", "thailand": "TH",
    "indonesia": "ID", "philippines": "PH", "japan": "JP", "china": "CN",
    "hong kong": "HK", "south korea": "KR", "taiwan": "TW", "vietnam": "VN",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "qatar": "QA", "oman": "OM", "bahrain": "BH", "kuwait": "KW",
    "brazil": "BR", "brasil": "BR", "mexico": "MX", "argentina": "AR",
    "chile": "CL", "colombia": "CO", "sri lanka": "LK", "nepal": "NP",
    "bhutan": "BT", "bangladesh": "BD", "kenya": "KE", "bermuda": "BM",
}


def country_code(value) -> Optional[str]:
    """Fold a country name or code to ISO-2, leaving unknowns untouched."""
    text = clean_text(value)
    if not text:
        return None
    mapped = _COUNTRY_ISO2.get(text.lower().strip(". "))
    if mapped:
        return mapped
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return text
