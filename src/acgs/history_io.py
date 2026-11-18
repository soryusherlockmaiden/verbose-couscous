"""Load content-pinned Binance public archives and point-in-time metadata."""

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from acgs.capture import stored_payload
from acgs.market_data import BASE_URL
from acgs.pit import (
    HOUR,
    ZERO,
    Candle,
    Dataset,
    Evidence,
    Filters,
    FilterWithdrawal,
    Funding,
    Instrument,
    Listing,
    Lot,
    Maintenance,
    Market,
    MarkGap,
    Prices,
    Provenance,
    Schedule,
    Spread,
    Tier,
)
from acgs.sim_config import digest, instant_ms


@dataclass(frozen=True)
class Loaded:
    market: Market
    digest: str
    manifest: dict[str, Any]


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def number(value: Any) -> Decimal:
    """An exact decimal from JSON text. A JSON float would import its binary expansion."""
    if type(value) is int:
        return Decimal(value)
    if not isinstance(value, str):
        raise ValueError("Expected a decimal string")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Expected a finite decimal")
    return result


def integer(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("Expected an integer")
    return value


def _time(value: str | int) -> int:
    if type(value) is int:
        return value
    if not isinstance(value, str):
        raise ValueError("Timestamp must be UTC milliseconds or ISO 8601")
    return instant_ms(datetime.fromisoformat(value))


def _evidence(row: dict[str, Any]) -> Evidence:
    return Evidence(
        row["source"], row["method"], Provenance(row["provenance"]),
        _time(row["as_of"]), row.get("limitation", ""),
    )


def _prices(row: list[str]) -> Prices:
    return Prices(*(Decimal(str(x)) for x in row[1:5]))


def _candles(
    rows: list[list[str]], inst: Instrument, source: str, size: int, start: int, end: int,
    delay: int, *, mark: bool,
) -> list[Candle]:
    result = []
    for row in rows:
        if len(row) != 12:
            raise ValueError("Expected 12 Binance USD-M kline fields")
        opening, inclusive_end = int(row[0]), int(row[6])
        closing = inclusive_end + 1
        if not start <= opening < closing <= end or closing - opening != size:
            raise ValueError(f"Kline outside declared interval or coverage: {source}")
        result.append(Candle(
            inst, opening, closing, closing + delay, _prices(row),
            ZERO if mark else Decimal(row[7]), ZERO if mark else Decimal(row[5]),
            Evidence(source, "usd_m_kline", Provenance.VERIFIED, closing), mark,
        ))
    return result


def _settlements(
    rows: list[list[str]], inst: Instrument, source: str, start: int, end: int, delay: int,
) -> list[Funding]:
    result = []
    for row in rows:
        if len(row) != 3:
            raise ValueError("Expected three Binance USD-M funding archive fields")
        time = int(row[0])
        if not start <= time < end:
            raise ValueError(f"Funding outside declared coverage: {source}")
        result.append(Funding(
            inst, time, Decimal(row[2]),
            Evidence(source, "usd_m_settlement_archive", Provenance.VERIFIED, time + delay),
            interval_ms=int(row[1]) * HOUR,
        ))
    return result


def _rest_settlements(
    raw: bytes, inst: Instrument, source: str, start: int, end: int, delay: int,
) -> list[Funding]:
    payload = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Expected USD-M fundingRate response array")
    result = []
    for row in payload:
        time = integer(row["fundingTime"])
        if row["symbol"] != inst.symbol or not start <= time < end:
            raise ValueError("Unexpected instrument/time in funding response")
        result.append(Funding(
            inst, time, number(row["fundingRate"]),
            Evidence(source, "usd_m_funding_rate_rest", Provenance.VERIFIED, time + delay),
            number(row["markPrice"]) if row.get("markPrice") else None,
        ))
    return result


def _attach_funding_marks(
    settlements: list[Funding], observations: list[Funding],
) -> list[Funding]:
    # Match the literal epoch, timestamp and rate. The sidecar can only add a mark
    # to an existing event; it cannot fill a gap or change decision-time spacing.
    events = {(row.instrument, row.time): row for row in settlements}
    for mark in observations:
        key = mark.instrument, mark.time
        event = events.get(key)
        if event is None or event.rate != mark.rate or mark.mark is None:
            raise ValueError("Funding mark does not match an archived instrument, settlement and rate")
        if event.interval_ms is None or event.mark is not None:
            raise ValueError("Funding mark requires a unique archive settlement without a mark")
        events[key] = replace(event, mark=mark.mark, mark_evidence=mark.evidence)
    return [events[(row.instrument, row.time)] for row in settlements]


def _archive_rows(payload: bytes, kind: str, limit: int = 48_000_000) -> list[list[str]]:
    with ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if (len(entries) != 1 or not entries[0].filename.endswith(".csv")
                or entries[0].file_size > limit):
            raise ValueError("Expected one bounded Binance archive CSV")
        with archive.open(entries[0]) as stream:
            rows = list(csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")))
    if rows:
        header = "calc_time" if kind == "fundingRate" else "open_time"
        if rows[0] and rows[0][0] == header:
            rows = rows[1:]
    if not rows:
        raise ValueError("Empty archive cannot establish coverage")
    return rows


def _lot(raw: dict[str, Any]) -> Lot:
    return Lot(number(raw["minQty"]), number(raw["maxQty"]), number(raw["stepSize"]))


def _filters(
    inst: Instrument, effective: int, ev: Evidence, raw: list[dict[str, Any]]
) -> Filters | None:
    read = {row["filterType"]: row for row in raw}
    if len(read) != len(raw):
        raise ValueError("Duplicate exchange filter")
    needed = {"PRICE_FILTER", "LOT_SIZE", "MARKET_LOT_SIZE", "MIN_NOTIONAL", "PERCENT_PRICE"}
    if not needed <= read.keys():
        return None
    price, band = read["PRICE_FILTER"], read["PERCENT_PRICE"]
    return Filters(
        inst, effective, ev, _lot(read["LOT_SIZE"]), _lot(read["MARKET_LOT_SIZE"]),
        number(price["tickSize"]), number(read["MIN_NOTIONAL"]["notional"]),
        number(price["minPrice"]), number(price["maxPrice"]),
        number(band["multiplierDown"]), number(band["multiplierUp"]),
    )


def _capture_observations(
    path: Path, wanted: set[str], cutoff: int,
    prior_listings: tuple[Listing, ...] = (),
) -> tuple[list[Listing], list[Filters | FilterWithdrawal], list[Schedule], list[Spread], list[str]]:
    """Import M0 captures with the manifest listings they can supersede."""
    listings: list[Listing] = []
    filters: list[Filters | FilterWithdrawal] = []
    schedules: list[Schedule] = []
    spreads: list[Spread] = []
    hashes: list[str] = []
    current: dict[str, Instrument] = {}
    # Every capture is a complete snapshot: what it omits is withdrawn from its receipt.
    known: dict[Instrument, Listing] = {}
    prior: dict[Instrument, list[Listing]] = {}
    for row in prior_listings:
        if not wanted or row.instrument.symbol in wanted:
            prior.setdefault(row.instrument, []).append(row)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema not in (1, 2):
            raise ValueError("Unsupported M0 capture schema")
        compression = "payload_encoding" if schema == 2 else "NULL"
        rows = connection.execute(
            f"""SELECT source_url, received_at, payload, sha256, {compression}
               FROM capture_attempts WHERE status = 'CAPTURED' AND provenance = 'VERIFIED'
               ORDER BY received_at, id"""
        )
        for url, stamp, payload, sha, encoding in rows:
            observation = datetime.fromisoformat(stamp)
            # Round observation UP to the millisecond so the snapshot is never backdated.
            base = observation.replace(microsecond=observation.microsecond // 1000 * 1000)
            time = instant_ms(base) + int(base != observation)
            if time > cutoff:
                break
            raw = stored_payload(payload, encoding)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("M0 capture content hash mismatch")
            parsed_url = urlsplit(url)
            if f"{parsed_url.scheme}://{parsed_url.netloc}" != BASE_URL:
                raise ValueError("M0 capture has an unsupported source")
            endpoint = parsed_url.path.rsplit("/", 1)[-1]
            ev = Evidence(url, "m0_capture:" + sha, Provenance.VERIFIED, time)
            body = json.loads(raw, object_pairs_hook=_unique)
            hashes.append(digest((url, stamp, sha)))
            if endpoint == "exchangeInfo":
                current = {}
                for item in body["symbols"]:
                    symbol = item["symbol"]
                    if wanted and symbol not in wanted:
                        continue
                    multiplier = re.match(r"^(1000000|100000|10000|1000)(?=[A-Z])", symbol)
                    inst = Instrument(
                        symbol, int(item["onboardDate"]), int(multiplier[1]) if multiplier else 1,
                    )
                    current[symbol] = inst
                    listing = Listing(
                        inst, time, ev, item["status"], item["quoteAsset"], item["contractType"],
                        item.get("underlyingType") in {"INDEX", "PREMARKET", "TRADIFI"},
                    )
                    listings.append(listing)
                    known[inst] = listing
                    snapshot: Filters | FilterWithdrawal | None = _filters(inst, time, ev, item["filters"])
                    if snapshot is None:
                        snapshot = FilterWithdrawal(
                            inst, time, Evidence(url, "exchangeInfo_filter_absence:" + sha,
                                                 Provenance.UNAVAILABLE, time,
                                                 "Latest exchangeInfo lacks a required filter"),
                        )
                    filters.append(snapshot)
                for epoch in sorted(known.keys() | prior.keys()):
                    if current.get(epoch.symbol) == epoch:
                        continue
                    observations = [
                        row for row in prior.get(epoch, [])
                        if row.effective <= time and row.evidence.as_of <= time
                    ]
                    if epoch in known:
                        observations.append(known[epoch])
                    latest = max(observations, key=lambda row: (row.effective, row.evidence.as_of), default=None)
                    if latest is None or not latest.tradable:
                        continue
                    withdrawal = Listing(
                        epoch, time,
                        Evidence(url, "exchangeInfo_absence:" + sha, Provenance.UNAVAILABLE, time,
                                 "Listing epoch missing from the latest exchangeInfo"),
                        "ABSENT_FROM_EXCHANGE_INFO",
                    )
                    listings.append(withdrawal)
                    known[epoch] = withdrawal
            elif endpoint in {"premiumIndex", "fundingInfo"}:
                present = set()
                for item in body:
                    observed_inst = current.get(item["symbol"])
                    if observed_inst is None:
                        continue
                    if endpoint == "fundingInfo":
                        present.add(observed_inst.symbol)
                        schedules.append(Schedule(
                            observed_inst, time, ev, int(item["fundingIntervalHours"]) * HOUR,
                            cap=number(item["adjustedFundingRateCap"]),
                            floor=number(item["adjustedFundingRateFloor"]),
                        ))
                    elif int(item["nextFundingTime"]) > time:
                        schedules.append(Schedule(observed_inst, time, ev, next_time=int(item["nextFundingTime"])))
                if endpoint == "fundingInfo":
                    absence = Evidence(url, "fundingInfo_absence:" + sha, Provenance.UNAVAILABLE,
                                       time, "Absence supplies no funding interval")
                    schedules.extend(Schedule(
                        current[symbol], time, absence, interval_withdrawn=True,
                    ) for symbol in current.keys() - present)
            elif endpoint == "depth":
                symbol = parse_qs(urlsplit(url).query).get("symbol", [""])[0]
                instrument = current.get(symbol)
                if instrument is not None and body["bids"] and body["asks"]:
                    bid, ask = number(body["bids"][0][0]), number(body["asks"][0][0])
                    if ZERO < bid <= ask:
                        spreads.append(Spread(instrument, ev, 20_000 * (ask - bid) / (ask + bid)))
    finally:
        connection.close()
    return listings, filters, schedules, spreads, hashes


def _asset(root: Path, info: dict[str, Any]) -> bytes:
    path = (root / str(info["path"])).resolve()
    if not path.is_relative_to(root.resolve()) or path.stat().st_size > 48_000_000:
        raise ValueError("Dataset asset path/size outside allowed bounds")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != info["sha256"]:
        raise ValueError(f"Dataset checksum mismatch: {info['path']}")
    return raw


def load_history(path: Path, root: Path, capture_db: Path | None = None) -> Loaded:
    manifest = read_json(path)
    if manifest.get("version") != "m1-history-v1":
        raise ValueError("Unknown historical dataset schema")
    ingestion = _time(manifest["ingested_at"])
    if ingestion < 0:
        raise ValueError("Invalid ingestion timestamp")
    delays = (manifest["candle_delay_ms"], manifest["funding_delay_ms"])
    if any(type(x) is not int or x < 0 for x in delays):
        raise ValueError("Expected nonnegative integral arrival delays")
    candle_delay, funding_delay = delays
    instrument_rows = [
        Instrument(row["symbol"], _time(row["onboard"]), integer(row.get("multiplier", 1)))
        for row in manifest["instruments"]
    ]
    instruments = {inst.id: inst for inst in instrument_rows}
    if len(instruments) != len(manifest["instruments"]):
        raise ValueError("Duplicate instrument ID")
    listings = [
        Listing(instruments[x["id"]], _time(x["effective"]), _evidence(x["evidence"]),
                x["status"], x.get("quote", "USDT"), x.get("contract", "PERPETUAL"),
                x.get("excluded_product", False))
        for x in manifest["listings"]
    ]
    filters: list[Filters | FilterWithdrawal] = []
    for row in manifest.get("filters", []):
        item = _filters(instruments[row["id"]], _time(row["effective"]),
                        _evidence(row["evidence"]), row["filters"])
        if item is None:
            raise ValueError("Critical exchange filter missing")
        filters.append(item)
    maintenance = [
        Maintenance(
            instruments[x["id"]], _time(x["effective"]), _evidence(x["evidence"]),
            tuple(Tier(number(t["ceiling"]), number(t["rate"]), number(t["deduction"]),
                       integer(t["max_leverage"])) for t in x["tiers"]),
        )
        for x in manifest.get("maintenance", [])
    ]
    spreads = [
        Spread(instruments[x["id"]], _evidence(x["evidence"]), number(x["bps"]))
        for x in manifest.get("spreads", [])
    ]
    schedules = [
        Schedule(
            instruments[x["id"]], _time(x["effective"]), _evidence(x["evidence"]),
            integer(x["interval_ms"]) if "interval_ms" in x else None,
            _time(x["next_time"]) if "next_time" in x else None,
            number(x["cap"]) if "cap" in x else None,
            number(x["floor"]) if "floor" in x else None,
            _time(x["until"]) if "until" in x else None,
        )
        for x in manifest.get("schedules", [])
    ]
    marks = tuple(
        MarkGap(instruments[x["id"]], _time(x["start"]), _time(x["end"]), _evidence(x["evidence"]))
        for x in manifest.get("mark_gaps", [])
    )
    candles: list[Candle] = []
    funding: list[Funding] = []
    funding_marks: list[Funding] = []
    for info in manifest["assets"]:
        inst = instruments[info["id"]]
        source = info["source"]
        start, end = _time(info["start"]), _time(info["end"])
        if not inst.onboard <= start < end <= ingestion:
            raise ValueError("Invalid declared archive coverage")
        kind = info["kind"]
        raw = _asset(root, info)
        if kind in {"klines", "markPriceKlines"}:
            candles.extend(_candles(
                _archive_rows(raw, kind), inst, source, info["interval_ms"], start, end,
                candle_delay, mark=kind == "markPriceKlines",
            ))
        elif kind == "fundingRate":
            funding.extend(_settlements(
                _archive_rows(raw, kind), inst, source, start, end, funding_delay,
            ))
        elif kind in {"fundingRest", "fundingMarks"}:
            target = funding_marks if kind == "fundingMarks" else funding
            target.extend(_rest_settlements(raw, inst, source, start, end, funding_delay))
        else:
            raise ValueError(f"Unknown historical asset type: {kind}")
    if funding_marks:
        funding = _attach_funding_marks(funding, funding_marks)
    hashes: list[str] = []
    if capture_db is not None:
        capture = manifest["capture"]
        captured_listings, captured_filters, captured_schedules, captured_spreads, hashes = _capture_observations(
            capture_db, set(capture["symbols"]), _time(capture["cutoff"]),
            tuple(listings),
        )
        if digest(hashes) != capture["digest"]:
            raise ValueError("Capture selection differs from the pinned manifest")
        listings.extend(captured_listings)
        filters.extend(captured_filters)
        schedules.extend(captured_schedules)
        spreads.extend(captured_spreads)
    elif "capture" in manifest:
        raise ValueError("Dataset requires the pinned M0 capture database")
    return Loaded(Market(Dataset(
        tuple(listings), tuple(candles), tuple(funding), tuple(schedules), tuple(filters),
        tuple(maintenance), tuple(spreads), marks,
    )), digest(manifest), manifest)
