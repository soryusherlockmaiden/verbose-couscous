"""Date-based access rules shared by M1 and M2 history tooling."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from acgs.sim_config import instant_ms

RECORD_PATH = Path(__file__).with_name("history_seal.json")
VERSION = "history-seal-v1"
ARCHIVE_HOST = "https://data.binance.vision"
CANDLES = ("klines", "markPriceKlines")
ARCHIVE = re.compile(
    r"https://data\.binance\.vision/data/futures/um/(?P<period>monthly|daily)/"
    r"(?P<kind>klines|markPriceKlines|fundingRate)/(?P<symbol>[A-Z0-9]+)/"
    r"(?:(?P<interval>[1-9][0-9]*[mhdwM])/(?P=symbol)-(?P=interval)|(?P=symbol))"
    r"-(?P<date>[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)\.zip"
)


class AccessRefused(ValueError):
    """A history operation is outside its permitted date range."""


def utc_ms(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() == timedelta(0):
            result = instant_ms(timestamp)
            if result >= 0:
                return result
    raise AccessRefused("Expected a UTC timestamp at millisecond resolution")


@dataclass(frozen=True)
class Window:
    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise AccessRefused("Expected a nonempty half-open UTC window")

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "Window":
        return cls(utc_ms(raw["start"]), utc_ms(raw["end"]))

    def contains(self, other: "Window") -> bool:
        return self.start <= other.start and other.end <= self.end


def _source_bundle(entry: Mapping[str, Any]) -> tuple[Window, str | None]:
    source = entry["source"]
    kind = entry["kind"]
    symbol = entry["id"].split("@", 1)[0]
    if kind in {"fundingMarks", "fundingRest"}:
        url = urlsplit(source)
        query = parse_qs(url.query, strict_parsing=True)
        if (
            url.scheme != "https" or url.netloc != "fapi.binance.com"
            or url.path != "/fapi/v1/fundingRate" or url.fragment
            or query.keys() != {"symbol", "startTime", "endTime", "limit"}
            or query["symbol"] != [symbol]
            or any(len(query[key]) != 1 or not re.fullmatch(r"[0-9]+", query[key][0])
                   for key in ("startTime", "endTime", "limit"))
        ):
            raise AccessRefused("Expected a bounded Binance USD-M settlement source")
        return Window(int(query["startTime"][0]), int(query["endTime"][0]) + 1), None

    asset = ARCHIVE.fullmatch(source)
    if asset is None or asset["symbol"] != symbol or asset["kind"] != kind:
        raise AccessRefused("Expected a canonical Binance USD-M archive URL")
    interval, label = asset["interval"], asset["date"]
    if (interval is not None) != (kind in CANDLES):
        raise AccessRefused("Unexpected interval in archive URL")
    monthly = asset["period"] == "monthly"
    if monthly != (len(label) == 7):
        raise AccessRefused("Archive period differs from URL date")
    start = datetime.fromisoformat(label + "-01" if monthly else label).replace(tzinfo=UTC)
    if monthly:
        end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    else:
        end = start + timedelta(days=1)
    return Window(instant_ms(start), instant_ms(end)), interval


@dataclass(frozen=True)
class HistorySeal:
    ranges: tuple[Window, ...]

    def seals_time(self, time: int) -> bool:
        return any(window.start <= time < window.end for window in self.ranges)

    def require_unsealed(self, window: Window, subject: str) -> None:
        if any(window.start < sealed.end and sealed.start < window.end for sealed in self.ranges):
            raise AccessRefused(f"{subject} overlaps a registered sealed range")

    def check_asset(self, entry: Mapping[str, Any]) -> tuple[Window, str | None]:
        bundle, interval = _source_bundle(entry)
        self.require_unsealed(bundle, "Source bundle")
        if not bundle.contains(Window.parse(entry)):
            raise AccessRefused("Declared asset coverage exceeds the source bundle")
        return bundle, interval

    def check_manifest(self, manifest: Mapping[str, Any]) -> None:
        for entry in manifest["assets"]:
            self.check_asset(entry)
        for field in ("listings", "filters", "maintenance", "spreads", "schedules", "mark_gaps"):
            if any(self.seals_time(utc_ms(row["evidence"]["as_of"])) for row in manifest.get(field, [])):
                raise AccessRefused(f"{field} observations include a registered sealed range")
        if "capture" in manifest:
            cutoff = utc_ms(manifest["capture"]["cutoff"])
            self.require_unsealed(Window(cutoff, cutoff + 1), "Capture cutoff")


def registered_seal() -> HistorySeal:
    raw = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    if raw.get("version") != VERSION or not raw.get("ranges"):
        raise AccessRefused("Expected a versioned history seal with registered ranges")
    return HistorySeal(tuple(Window.parse(row) for row in raw["ranges"]))
