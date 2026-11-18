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
SETTLEMENT_REST = ("fundingMarks", "fundingRest")
METADATA = ("listings", "filters", "maintenance", "spreads", "schedules", "mark_gaps")
# Instrument onboard instants are identity anchors and stay admissible. Event
# times and spans in inline metadata are sealed when observed at or after the
# start of the range they describe. A forecast published earlier stays admissible.
EVENT_TIMES = ("effective", "next_time")
RESERVE = "m2-retrospective-reserve"
HOLDOUT = "m2-prospective-holdout"
PURPOSES = (RESERVE, HOLDOUT)
EVENT_SPANS = (("start", "end"), ("effective", "until"))
ARCHIVE = re.compile(
    r"https://data\.binance\.vision/data/futures/um/(?P<period>monthly|daily)/"
    r"(?P<kind>klines|markPriceKlines|fundingRate)/(?P<symbol>[A-Z0-9]+)/"
    r"(?:(?P<interval>[1-9][0-9]*[mhdwM])/(?P=symbol)-(?P=interval)|(?P=symbol)-fundingRate)"
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
    if kind in SETTLEMENT_REST:
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


def resolve_asset_path(root: Path, entry: Mapping[str, Any]) -> Path:
    """Resolve the local target of an asset whose source has passed preflight."""
    target = Path(root, entry["path"]).resolve()
    if not target.is_relative_to(root.resolve()):
        raise AccessRefused("Dataset asset path outside allowed root")
    if entry["kind"] in SETTLEMENT_REST:
        if target.suffix != ".json":
            raise AccessRefused("Asset filename for a settlement response must end in .json")
    elif target.name != entry["source"].rsplit("/", 1)[-1]:
        raise AccessRefused("Asset filename must match the source archive")
    return target


@dataclass(frozen=True)
class HistorySeal:
    ranges: tuple[Window, ...]
    # Registered holdouts also bound research chronology: approved data ends before each.
    holdouts: tuple[Window, ...] = ()
    reserves: tuple[Window, ...] = ()

    def seals_time(self, time: int) -> bool:
        return any(window.start <= time < window.end for window in self.ranges)

    def permits_prospective_capture_selection(self, receipt: int, slot: Window) -> bool:
        """Allow M0 scheduling from a receipt inside the same prospective holdout."""
        if any(
            reserve.start <= receipt < reserve.end or (slot.start < reserve.end and reserve.start < slot.end)
            for reserve in self.reserves
        ):
            return False
        return any(window.start <= receipt < slot.start and window.contains(slot) for window in self.holdouts)

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
        for field in METADATA:
            for row in manifest.get(field, []):
                as_of = utc_ms(row["evidence"]["as_of"])
                if self.seals_time(as_of):
                    raise AccessRefused(f"{field} observations include a registered sealed range")
                # A later observation can still describe the sealed period, e.g. a
                # backfilled status change or a gap. Seal what was learned at or after
                # the range began; the M0 capture path follows the same rule because
                # its event times are its receipt times.
                described = [Window(utc_ms(row[start]), utc_ms(row[end]))
                             for start, end in EVENT_SPANS if start in row and end in row]
                described.extend(Window(t, t + 1) for t in (
                    utc_ms(row[key]) for key in EVENT_TIMES if key in row))
                if any(
                    sealed.start <= as_of and span.start < sealed.end and sealed.start < span.end
                    for sealed in self.ranges for span in described
                ):
                    raise AccessRefused(f"{field} evidence describes a registered sealed range")
        if "capture" in manifest:
            cutoff = utc_ms(manifest["capture"]["cutoff"])
            self.require_unsealed(Window(cutoff, cutoff + 1), "Capture cutoff")


def registered_seal() -> HistorySeal:
    raw = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    if raw.get("version") != VERSION or not raw.get("ranges"):
        raise AccessRefused("Expected a versioned history seal with registered ranges")
    if any(row.get("purpose") not in PURPOSES for row in raw["ranges"]):
        raise AccessRefused("Each registered sealed range needs a known purpose")
    ranges = tuple(Window.parse(row) for row in raw["ranges"])
    by_purpose = {
        purpose: tuple(window for window, row in zip(ranges, raw["ranges"], strict=True) if row["purpose"] == purpose)
        for purpose in PURPOSES
    }
    return HistorySeal(ranges, by_purpose[HOLDOUT], by_purpose[RESERVE])
