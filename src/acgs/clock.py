from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant


def utc_iso(instant: datetime) -> str:
    """Fixed-width UTC timestamp whose string order matches time order."""
    if instant.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware")
    return instant.astimezone(UTC).isoformat(timespec="microseconds")
