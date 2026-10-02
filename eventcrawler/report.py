"""Per-run bookkeeping: what each source returned, and why anything was skipped.

Written alongside events.json so a run is auditable - especially the sources that
are blocked, which would otherwise just look like zero results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

from . import normalize as nz

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"
STATUS_EMPTY = "empty"
STATUS_SKIPPED = "skipped"
STATUS_CARRIED = "carried"  # kept from a previous run, not refreshed this time


@dataclass
class SiteResult:
    key: str
    name: str
    status: str = STATUS_OK
    events: int = 0
    http_status: Optional[int] = None
    requires_js: bool = False
    reason: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunReport:
    started_at: str = field(default_factory=nz.now_iso)
    finished_at: Optional[str] = None
    sites: List[SiteResult] = field(default_factory=list)
    total_events: int = 0
    duplicates_dropped: int = 0
    rejected_no_url: int = 0
    rejected_non_sport: int = 0
    rejected_past: int = 0
    requests_made: int = 0

    def add(self, result: SiteResult) -> SiteResult:
        self.sites.append(result)
        return result

    @property
    def blocked(self) -> List[SiteResult]:
        return [s for s in self.sites if s.status == STATUS_BLOCKED]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sites"] = [s.to_dict() for s in self.sites]
        return data

    def console_summary(self) -> str:
        width = max([len(s.key) for s in self.sites] + [6])
        lines = ["", "  SOURCE".ljust(width + 4) + "STATUS      EVENTS  NOTE", "  " + "-" * (width + 46)]
        for site in self.sites:
            note = site.reason or ""
            if site.http_status and site.status != STATUS_OK:
                note = f"HTTP {site.http_status}" + (f" - {note}" if note else "")
            if site.errors and not note:
                note = site.errors[0][:44]
            lines.append(
                f"  {site.key.ljust(width + 2)}{site.status.ljust(12)}{str(site.events).rjust(6)}  {note[:52]}"
            )
        lines.append("")
        lines.append(
            f"  total {self.total_events} events "
            f"({self.duplicates_dropped} duplicates dropped, "
            f"{self.rejected_no_url} rejected for no url, "
            f"{self.rejected_non_sport} non-sport filtered, "
            f"{self.rejected_past} already finished) "
            f"in {self.requests_made} requests"
        )
        return "\n".join(lines)
