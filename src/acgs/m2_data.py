"""Phase A access boundary for checksum-pinned OFFLINE market history."""

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from acgs.history_io import (
    MAX_HISTORY_ASSET,
    Loaded,
    integer,
    load_history_manifest,
    read_history_asset,
    read_json,
)
from acgs.market_data import PublicResponse, fetch_public
from acgs.pit import DAY, HOUR, Instrument
from acgs.sim_config import digest, instant_ms

INTERVALS = {"15m": HOUR // 4, "1h": HOUR, "1d": DAY}
SIGNAL_INTERVALS = ("15m", "1h")
OWNERSHIP_WINDOW_MS = 15_000  # Accepted m1-v3 contract.
ARCHIVE_HOST = "https://data.binance.vision"
MANIFEST_SCHEMA = "m2-history-v1"
CANDLES = ("klines", "markPriceKlines")
ARCHIVE = re.compile(
    r"https://data\.binance\.vision/data/futures/um/(?P<period>monthly|daily)/"
    r"(?P<kind>klines|markPriceKlines|fundingRate)/(?P<symbol>[A-Z0-9]+)/"
    r"(?:(?P<interval>15m|1h|1d)/)?(?P=symbol)"
    r"(?:-(?P=interval))?-(?P<date>[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)\.zip"
)
SHA256 = re.compile(r"[0-9a-f]{64}")
METADATA = ("listings", "filters", "maintenance", "spreads", "schedules", "mark_gaps")


class AccessRefused(ValueError):
    """A request has no approved access to these inputs."""


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

    def contains(self, other: "Window") -> bool:
        return self.start <= other.start < other.end <= self.end


def _window(raw: dict[str, Any]) -> Window:
    return Window(utc_ms(raw["start"]), utc_ms(raw["end"]))


def _dependencies(request: Window, approval: dict[str, Any]) -> dict[str, Window]:
    bars = integer(approval["signal_warmup_bars"])
    days = integer(approval["history_days"])
    prior = integer(approval["funding_lookback_ms"])
    tail = integer(approval["funding_lookahead_ms"])
    if min(bars, days, prior) <= 0 or tail <= OWNERSHIP_WINDOW_MS:
        raise AccessRefused("Explicit history and funding dependency bounds are required")
    result = {"decision": request}
    for name in SIGNAL_INTERVALS:
        size = INTERVALS[name]
        open_start = request.start // size * size
        next_open = (request.end + size - 1) // size * size
        result[f"klines:{name}"] = Window(open_start - bars * size, next_open + size)
        result[f"markPriceKlines:{name}"] = Window(open_start, next_open + size)
    day_start = request.start // DAY * DAY
    day_end = (request.end + DAY - 1) // DAY * DAY
    result["klines:1d"] = Window(day_start - (days + 1) * DAY, day_end)
    result["markPriceKlines:1d"] = result["klines:1d"]
    # Eligibility can check funding continuity over the entire daily-history
    # requirement. A prefix and accounting lookahead flank that requirement.
    result["fundingRate"] = Window(
        day_start - (days + 1) * DAY - prior,
        (request.end + HOUR - 1) // HOUR * HOUR + tail,
    )
    return result


def _archive_window(entry: dict[str, Any], symbol: str) -> Window:
    asset = ARCHIVE.fullmatch(entry["source"])
    if asset is None or asset["symbol"] != symbol or asset["kind"] != entry["kind"]:
        raise AccessRefused("Expected a canonical Binance USD-M archive URL")
    if entry["path"] != entry["source"].removeprefix(ARCHIVE_HOST + "/"):
        raise AccessRefused("Archive path must match its source")
    interval, label = asset["interval"], asset["date"]
    if asset["kind"] in CANDLES:
        if interval is None or integer(entry["interval_ms"]) != INTERVALS[interval]:
            raise AccessRefused("Archive interval differs from manifest")
    elif interval is not None or "interval_ms" in entry:
        raise AccessRefused("Unexpected funding archive interval")
    monthly = asset["period"] == "monthly"
    if monthly != (len(label) == 7):
        raise AccessRefused("Archive period differs from URL date")
    name = f"{symbol}-{interval}" if interval else symbol
    directory = f"{symbol}/{interval}" if interval else symbol
    expected = f"{ARCHIVE_HOST}/data/futures/um/{asset['period']}/{asset['kind']}"
    if entry["source"] != f"{expected}/{directory}/{name}-{label}.zip":
        raise AccessRefused("Expected canonical archive directory and file name")
    start = datetime.fromisoformat(label + "-01" if monthly else label).replace(tzinfo=UTC)
    if monthly:
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        end = start + timedelta(days=1)
    return Window(instant_ms(start), instant_ms(end))


def _settlement_marks(entry: dict[str, Any], symbol: str, window: Window) -> None:
    # A dated, bounded REST export may be supplied by the operator. Only the
    # three public archive families are fetched automatically.
    query = (
        f"symbol={symbol}&startTime={window.start}&endTime={window.end - 1}&limit=1000"
    )
    if (
        entry["source"] != f"https://fapi.binance.com/fapi/v1/fundingRate?{query}"
        or entry["path"] != f"local/fundingMarks/{symbol}-{window.start}-{window.end}.json"
        or "interval_ms" in entry
    ):
        raise AccessRefused("Expected a bounded local settlement-mark export")


@dataclass(frozen=True)
class _Approved:
    manifest: dict[str, Any]
    dependencies: dict[str, Window]
    config_digest: str
    policy_digest: str
    request: Window
    purpose: str


def _approve(
    config_path: Path, manifest_path: Path, purpose: str, request: Window | None,
) -> _Approved:
    if purpose not in {"development", "validation"}:
        raise AccessRefused("Holdout is sealed. Only development and validation are supported.")
    config = read_json(config_path)
    if (
        config.get("version") != "m2-v1" or config.get("mode") != "OFFLINE"
        or config.get("protocol_status") not in {"DRAFT", "FROZEN"}
    ):
        raise AccessRefused("Expected a versioned OFFLINE M2 configuration")
    policy = config["m1"]
    if (
        policy["replay"] != "m1-replay-v8" or policy["policy"] != "m1-v3"
        or not SHA256.fullmatch(policy["policy_sha256"])
    ):
        raise AccessRefused("Expected a pinned accepted M1 execution contract")
    access = config["access"]
    approval = access["approvals"].get(purpose)
    if access["holdout"] is None or approval is None:
        raise AccessRefused("Holdout boundary or dataset approval is OPEN")
    holdout = _window(access["holdout"])
    safe = _window(approval["data_window"])
    decisions = _window(approval["decision_window"])
    request = decisions if request is None else request
    if safe.end > holdout.start:
        raise AccessRefused("Approved data window crosses the holdout seal")
    if request.end > holdout.start:
        raise AccessRefused("Decision request crosses the holdout seal")
    if not safe.contains(decisions) or not decisions.contains(request):
        raise AccessRefused("Decision request outside the approved window")
    dependencies = _dependencies(request, approval)
    for name, dependency in dependencies.items():
        if not safe.contains(dependency):
            raise AccessRefused(f"{name} dependency exceeds the approved pre-holdout data window")

    manifest = read_json(manifest_path)
    if (
        manifest.get("version") != MANIFEST_SCHEMA or manifest.get("m2_purpose") != purpose
        or digest(manifest) != approval["manifest_sha256"]
        or manifest.keys() - {
            "version", "m2_purpose", "ingested_at", "candle_delay_ms", "funding_delay_ms",
            "instruments", "assets", *METADATA,
        }
    ):
        raise AccessRefused("Manifest scope, schema or approved digest mismatch")
    instruments = {}
    for row in manifest["instruments"]:
        instrument = Instrument(row["symbol"], utc_ms(row["onboard"]), row.get("multiplier", 1))
        if instrument.onboard >= safe.end or instrument.id in instruments:
            raise AccessRefused("Duplicate or out-of-scope epoch")
        instruments[instrument.id] = instrument
    if not manifest["assets"]:
        raise AccessRefused("Manifest contains no assets")
    for name in METADATA:
        for row in manifest.get(name, []):
            if name == "listings" and not {"quote", "contract", "excluded_product"} <= row.keys():
                raise AccessRefused("Listing evidence needs explicit instrument classification")
            times = [utc_ms(row["evidence"]["as_of"])]
            times.extend(utc_ms(row[key]) for key in ("effective", "start", "next_time") if key in row)
            if max(times) >= safe.end or any(
                utc_ms(row[key]) > safe.end for key in ("end", "until") if key in row
            ):
                raise AccessRefused(f"{name} evidence exceeds the approved data window")
    # Reuse M1 validation of inline evidence before either I/O entry point.
    load_history_manifest(_m1_manifest(manifest, []), manifest_path.parent)

    ingestion = utc_ms(manifest["ingested_at"])
    paths: set[str] = set()
    streams: dict[tuple[str, str, int | None], list[Window]] = defaultdict(list)
    for entry in manifest["assets"]:
        fields = {"id", "kind", "path", "source", "sha256", "size_bytes", "start", "end"}
        if entry["kind"] in CANDLES:
            fields.add("interval_ms")
        if entry.keys() != fields:
            raise AccessRefused("Unexpected or missing asset fields")
        window = _window(entry)
        inst = instruments[entry["id"]]
        if (
            not SHA256.fullmatch(entry["sha256"])
            or not 0 < integer(entry["size_bytes"]) <= MAX_HISTORY_ASSET
            or not inst.onboard <= window.start < window.end <= ingestion
            or entry["path"] in paths
        ):
            raise AccessRefused("Invalid asset pin, coverage, epoch or duplicate path")
        paths.add(entry["path"])
        if entry["kind"] == "fundingMarks":
            _settlement_marks(entry, inst.symbol, window)
            bundle = window
        else:
            bundle = _archive_window(entry, inst.symbol)
        if not safe.contains(bundle) or not bundle.contains(window):
            raise AccessRefused(f"Asset bundle exceeds approved pre-holdout coverage: {entry['path']}")
        streams[inst.id, entry["kind"], entry.get("interval_ms")].append(window)
    for windows in streams.values():
        ordered = sorted(windows, key=lambda window: window.start)
        if any(left.end > right.start for left, right in zip(ordered, ordered[1:], strict=False)):
            raise AccessRefused("Declared coverage overlaps for an epoch and asset stream")
    return _Approved(manifest, dependencies, digest(config), policy["policy_sha256"], request, purpose)


def _m1_manifest(manifest: dict[str, Any], assets: list[Any]) -> dict[str, Any]:
    return {**manifest, "version": "m1-history-v1", "assets": assets}


def _paths(root: Path, manifest: dict[str, Any]) -> list[Path]:
    base = root.absolute()
    paths = [base / entry["path"] for entry in manifest["assets"]]
    if any(target.resolve() != target for target in paths):
        raise AccessRefused("Asset roots and paths must be canonical and may not use aliases")
    return paths


def load_research_inputs(
    config: Path, manifest: Path, root: Path, purpose: str, request: Window,
) -> Loaded:
    """Load approved research inputs. All metadata is preflighted before payload reads."""
    approved = _approve(config, manifest, purpose, request)
    return _load(root, approved)


def _load(root: Path, approved: _Approved) -> Loaded:
    _paths(root, approved.manifest)
    market = load_history_manifest(
        _m1_manifest(approved.manifest, approved.manifest["assets"]), root,
    ).market
    return Loaded(market, digest(approved.manifest), approved.manifest)


def _fetch_archive(url: str) -> PublicResponse:
    return fetch_public(url, max_payload=MAX_HISTORY_ASSET)


def fetch_inputs(
    config: Path, manifest: Path, root: Path, purpose: str,
    *, fetch: Callable[[str], PublicResponse] = _fetch_archive,
) -> dict[str, Any]:
    """Validate the entire manifest and acquisition scope before the first GET."""
    approved = _approve(config, manifest, purpose, None)
    paths = _paths(root, approved.manifest)
    pending = []
    for entry, target in zip(approved.manifest["assets"], paths, strict=True):
        if target.exists() or entry["kind"] == "fundingMarks":
            read_history_asset(root, entry)
        else:
            pending.append((entry, target))
    for entry, target in pending:
        response = fetch(entry["source"])
        if response.status != 200:
            raise AccessRefused(f"Public archive unavailable or restricted (HTTP {response.status})")
        raw = response.payload
        if len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise AccessRefused(f"Archive differs from pinned size or checksum: {entry['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stage complete verified bytes and install atomically. Concurrent fetches
        # of the same manifest produce identical bytes.
        staged = None
        try:
            with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                staged = Path(stream.name)
                stream.write(raw)
            staged.replace(target)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    return {
        "manifest_sha256": digest(approved.manifest),
        "fetched_bytes": sum(entry["size_bytes"] for entry, _ in pending),
        "total_bytes": sum(entry["size_bytes"] for entry in approved.manifest["assets"]),
    }
