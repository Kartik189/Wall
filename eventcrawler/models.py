"""The normalized event record that every source is mapped onto."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import List, Optional

#: Canonical sports. Anything we cannot classify confidently becomes "other".
SPORTS = (
    "running",
    "trail_running",
    "triathlon",
    "duathlon",
    "cycling",
    "swimming",
    "hyrox",
    "adventure",
    "walking",
    "other",
)

DATE_OK = "ok"
#: Used when a source genuinely does not publish a date we can read without a
#: headless browser. We record the gap instead of inventing a date.
DATE_UNAVAILABLE = "unavailable_phase1"


def make_id(source: str, source_url: str) -> str:
    """Stable id for an event. Dedupe keys off this, so source_url must be set."""
    return hashlib.sha1(f"{source}|{source_url}".encode("utf-8")).hexdigest()[:16]


@dataclass
class Event:
    # --- identity (required) ---
    title: str
    source: str
    source_url: str  # canonical event detail page
    id: str = ""

    description: Optional[str] = None

    # --- when ---
    start: Optional[str] = None  # ISO-8601
    end: Optional[str] = None
    date_status: str = DATE_OK

    # --- sport ---
    sport: str = "other"
    distances: List[str] = field(default_factory=list)      # ["5K", "Half Marathon"]
    distance_km: List[float] = field(default_factory=list)  # [5.0, 21.098]
    event_subtypes: List[str] = field(default_factory=list)  # raw source labels

    # --- where ---
    is_online: bool = False
    venue_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # --- cost ---
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: Optional[str] = None

    # --- links ---
    ticket_url: Optional[str] = None    # registration / booking
    found_on_url: Optional[str] = None  # listing page we discovered it on
    image_url: Optional[str] = None

    # --- provenance ---
    organizer: Optional[str] = None
    status: Optional[str] = None  # EventScheduled / EventCancelled / ...
    scraped_at: str = ""

    def __post_init__(self) -> None:
        if not self.id and self.source_url:
            self.id = make_id(self.source, self.source_url)

    def to_dict(self) -> dict:
        return asdict(self)
