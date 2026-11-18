"""Phase A access boundary for checksum-pinned OFFLINE market history."""

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
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
from acgs.history_seal import ARCHIVE_HOST as ARCHIVE_HOST
from acgs.history_seal import CANDLES, METADATA, registered_seal
from acgs.history_seal import AccessRefused as AccessRefused
from acgs.history_seal import Window as Window
from acgs.history_seal import utc_ms as utc_ms
from acgs.m2_semantics import ACCEPTED_M1_SEMANTICS
from acgs.m2_semantics import VERSION as SEMANTICS_VERSION
from acgs.market_data import PublicResponse, fetch_public
from acgs.pit import DAY, HOUR, Instrument
from acgs.sim_config import digest

INTERVALS = {"15m": HOUR // 4, "1h": HOUR, "1d": DAY}
SIGNAL_INTERVALS = ("15m", "1h")
MANIFEST_SCHEMA = "m2-history-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
TIER_ANNOUNCEMENT = re.compile(
    r"https://(?:www\.)?binance\.com/(?:[a-z]{2}(?:-[A-Z]{2})?/)?"
    r"support/announcement/(?:detail/)?[A-Za-z0-9-]+"
)


def _dependencies(request: Window, approval: dict[str, Any]) -> dict[str, Window]:
    bars = integer(approval["signal_warmup_bars"])
    days = integer(approval["history_days"])
    prior = integer(approval["funding_lookback_ms"])
    tail = integer(approval["funding_lookahead_ms"])
    if min(bars, days, prior) <= 0 or tail <= ACCEPTED_M1_SEMANTICS.funding_ownership_window_ms:
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
    request: Window
    purpose: str


def _approve(
    config_path: Path, manifest_path: Path, purpose: str, request: Window | None,
) -> _Approved:
    if purpose not in {"development", "validation"}:
        raise AccessRefused("Holdout is sealed. Only development and validation are supported.")
    config = read_json(config_path)
    if (
        config.get("version") != "m2-v3" or config.get("mode") != "OFFLINE"
    ):
        raise AccessRefused("Expected an OFFLINE M2 configuration with schema m2-v3")
    if config.get("protocol_status") != "DRAFT":
        raise AccessRefused("Phase A requires a DRAFT protocol; frozen authorization is unavailable")
    semantics = config["m1_semantics"]
    if (
        semantics["version"] != SEMANTICS_VERSION
        or semantics["sha256"] != ACCEPTED_M1_SEMANTICS.digest
    ):
        raise AccessRefused("Expected the pinned M1 semantics")
    seal = registered_seal()
    access = config["access"]
    approval = access["approvals"].get(purpose)
    cutoff = access.get("research_cutoff")
    if access["holdout"] is None or cutoff is None or approval is None:
        raise AccessRefused("Holdout coverage, research cutoff or dataset approval is OPEN")
    envelope = Window.parse(access["holdout"])
    research_cutoff = utc_ms(cutoff)
    if not all(envelope.contains(registered) for registered in seal.ranges):
        raise AccessRefused("Configured holdout must cover every registered sealed range")
    if any(research_cutoff > registered.start for registered in seal.holdouts):
        raise AccessRefused("Research cutoff falls after a registered holdout starts")
    safe = Window.parse(approval["data_window"])
    decisions = Window.parse(approval["decision_window"])
    request = decisions if request is None else request
    # The covering envelope can span an unsealed gap. Only actual registry
    # ranges are sealed, while the explicit cutoff bounds research chronology.
    seal.require_unsealed(safe, "Approved data window")
    if safe.end > research_cutoff:
        raise AccessRefused("Approved data window crosses the research cutoff")
    if request.end > research_cutoff:
        raise AccessRefused("Decision request crosses the research cutoff")
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
            if name == "maintenance" and not TIER_ANNOUNCEMENT.fullmatch(row["evidence"]["source"]):
                raise AccessRefused("M2 maintenance tiers require a dated Binance announcement")
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
        window = Window.parse(entry)
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
        elif entry["kind"] not in {*CANDLES, "fundingRate"}:
            raise AccessRefused("Unsupported M2 asset kind")
        elif entry["path"] != entry["source"].removeprefix(ARCHIVE_HOST + "/"):
            raise AccessRefused("Archive path must match its source")
        bundle, interval = seal.check_asset(entry)
        if interval is not None and (
            interval not in INTERVALS or integer(entry["interval_ms"]) != INTERVALS[interval]
        ):
            raise AccessRefused("Archive interval differs from manifest")
        if not safe.contains(bundle):
            raise AccessRefused(f"Asset bundle exceeds approved pre-holdout coverage: {entry['path']}")
        streams[inst.id, entry["kind"], entry.get("interval_ms")].append(window)
    for windows in streams.values():
        ordered = sorted(windows, key=lambda window: window.start)
        if any(left.end > right.start for left, right in zip(ordered, ordered[1:], strict=False)):
            raise AccessRefused("Declared coverage overlaps for an epoch and asset stream")
    return _Approved(manifest, dependencies, digest(config), request, purpose)


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
