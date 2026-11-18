import hashlib
import json
import sqlite3
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from urllib.parse import urlencode

from acgs.clock import Clock, utc_iso
from acgs.config import CaptureConfig, Config, DepthTargets
from acgs.history_seal import Window, registered_seal
from acgs.ledger import insert_state_event
from acgs.market_data import BASE_URL, PublicResponse, retry_delay, valid_payload
from acgs.sim_config import instant_ms

DAILY = 86_400
PAYLOAD_ENCODING = "zlib"
RESTRICTED = "ACCESS_RESTRICTED"
BLOCKED = "BLOCKED_ACCESS_RESTRICTED"
PROSPECTIVE_SELECTION = "PROSPECTIVE_HOLDOUT_EXCHANGE_INFO"
EXCHANGE_INFO = f"{BASE_URL}/fapi/v1/exchangeInfo"
# The USD-M fundingInfo endpoint has weight zero and a separate shared IP limit.
# Charge it one unit locally as well. It remains scheduled once per UTC day.
REQUEST_WEIGHT = {"exchangeInfo": 1, "fundingInfo": 1, "premiumIndex": 10, "depth": 2}
MINUTE = timedelta(minutes=1)


def _depth_url(symbol: str) -> str:
    return f"{BASE_URL}/fapi/v1/depth?" + urlencode({"symbol": symbol, "limit": 5})


def _targets(config: CaptureConfig, symbols: tuple[str, ...]) -> Iterator[tuple[str, str, int]]:
    yield "exchangeInfo", EXCHANGE_INFO, DAILY
    yield "fundingInfo", f"{BASE_URL}/fapi/v1/fundingInfo", DAILY
    yield "premiumIndex", f"{BASE_URL}/fapi/v1/premiumIndex", config.interval_seconds
    for symbol in symbols:
        yield "depth", _depth_url(symbol), config.interval_seconds


def _slot(now: datetime, period: int) -> str:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % period, UTC).isoformat()


def restriction_open(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            """SELECT 1 FROM capture_attempts
               WHERE status = 'ACCESS_RESTRICTED' AND cleared_at IS NULL LIMIT 1"""
        ).fetchone()
        is not None
    )


def clear_access_restriction(
    connection: sqlite3.Connection, config: Config, clock: Clock
) -> int:
    """Record the operator's review of an HTTP 403/451 stop and clear the latch."""
    instant = clock.now()
    cleared_at = utc_iso(instant)
    with connection:
        cleared = connection.execute(
            """UPDATE capture_attempts SET cleared_at = ?
               WHERE status = 'ACCESS_RESTRICTED' AND cleared_at IS NULL""",
            (cleared_at,),
        ).rowcount
        if cleared:
            insert_state_event(
                connection,
                config,
                f"capture-restriction-cleared:{cleared_at}",
                instant,
                "CAPTURE_RESTRICTION_CLEARED",
            )
    return cleared


def stored_payload(payload: bytes, encoding: str | None) -> bytes:
    """Return the raw response bytes that the stored SHA-256 covers."""
    if encoding is None:
        return payload
    if encoding == PAYLOAD_ENCODING:
        return zlib.decompress(payload)
    raise ValueError("Unknown capture payload encoding")


@dataclass(frozen=True)
class _DepthCycle:
    slot: str
    ends_at: datetime
    expected: tuple[str, ...] | None
    targets: tuple[str, ...]
    closed: bool = False
    uses_holdout_snapshot: bool = False


def _eligible_symbols(raw: bytes) -> tuple[str, ...]:
    if not valid_payload("exchangeInfo", raw):
        raise ValueError("Invalid exchange snapshot")
    symbols: set[str] = set()
    eligible: list[str] = []
    for row in json.loads(raw)["symbols"]:
        symbol = row["symbol"]
        quote = row.get("quoteAsset")
        if symbol in symbols or not isinstance(quote, str) or not quote:
            raise ValueError("Ambiguous symbol classification in exchange snapshot")
        symbols.add(symbol)
        if (row["status"], quote, row["contractType"]) == ("TRADING", "USDT", "PERPETUAL"):
            eligible.append(symbol)
    return tuple(sorted(eligible))


def _depth_selection(
    connection: sqlite3.Connection, start: datetime, end: datetime, max_age: int,
) -> tuple[str, tuple[str, ...] | None, str | None, str | None]:
    latest = connection.execute(
        """SELECT id, received_at, sha256 FROM capture_attempts
           WHERE source_url = ? AND status = 'CAPTURED' AND received_at < ?
           ORDER BY received_at DESC, id DESC LIMIT 1""",
        (EXCHANGE_INFO, utc_iso(start)),
    ).fetchone()
    if latest is None:
        return "MISSING_EXCHANGE_INFO", None, None, None
    row_id, stamp, sha = latest
    try:
        received = datetime.fromisoformat(stamp)
        utc_iso(received)
    except ValueError:
        return "INVALID_EXCHANGE_INFO", None, stamp, sha
    if start - received > timedelta(seconds=max_age):
        return "STALE_EXCHANGE_INFO", None, stamp, sha
    floor_ms = received.replace(microsecond=received.microsecond // 1_000 * 1_000)
    receipt = instant_ms(floor_ms)
    seal = registered_seal()
    selection = "EXCHANGE_INFO"
    if seal.seals_time(receipt):
        if not seal.permits_prospective_capture_selection(receipt, Window(instant_ms(start), instant_ms(end))):
            return "SEALED_EXCHANGE_INFO", None, stamp, sha
        selection = PROSPECTIVE_SELECTION
    payload, encoding = connection.execute(
        "SELECT payload, payload_encoding FROM capture_attempts WHERE id = ?", (row_id,),
    ).fetchone()
    try:
        raw = stored_payload(payload, encoding)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("Exchange snapshot checksum mismatch")
        return selection, _eligible_symbols(raw), stamp, sha
    except (ValueError, zlib.error):
        return "INVALID_EXCHANGE_INFO", None, stamp, sha


def _close_cycle(connection: sqlite3.Connection, cycle: _DepthCycle, *, expired: bool) -> None:
    attempts = {
        url: (status, stamp) for url, status, stamp in connection.execute(
            """SELECT source_url, status, received_at FROM capture_attempts
               WHERE slot = ? AND source_url LIKE ?
               ORDER BY (status = 'CAPTURED'), id""",
            (cycle.slot, f"{BASE_URL}/fapi/v1/depth?%"),
        )
    }
    ends_at = utc_iso(cycle.ends_at)
    statuses = {}
    for symbol in cycle.targets:
        status, stamp = attempts.get(_depth_url(symbol), ("UNATTEMPTED", ""))
        statuses[symbol] = "RECEIVED_AFTER_SLOT" if status == "CAPTURED" and stamp >= ends_at else status
    if not expired and any(status != "CAPTURED" for status in statuses.values()):
        return
    expected = None if cycle.expected is None else {symbol: statuses[symbol] for symbol in cycle.expected}
    coverage = {
        "expected": None if expected is None else len(expected),
        "captured": None if expected is None else sum(status == "CAPTURED" for status in expected.values()),
        "missed": None if expected is None else {
            symbol: status for symbol, status in expected.items() if status != "CAPTURED"
        },
        "configured_only": {
            symbol: status for symbol, status in statuses.items() if expected is None or symbol not in expected
        },
    }
    connection.execute(
        "UPDATE capture_depth_cycles SET coverage = ? WHERE slot = ?",
        (json.dumps(coverage, sort_keys=True), cycle.slot),
    )


def _depth_cycle(
    connection: sqlite3.Connection, config: CaptureConfig, now: datetime,
) -> tuple[_DepthCycle, str | None]:
    utc_iso(now)
    latest = connection.execute(
        """SELECT slot, ends_at, expected_symbols, targets, coverage, selection
           FROM capture_depth_cycles ORDER BY slot DESC LIMIT 1"""
    ).fetchone()
    slot = _slot(now, config.interval_seconds)
    start = datetime.fromisoformat(slot)
    gap_from = None
    with connection:
        if latest is not None:
            old_slot, end, expected, targets, coverage, prior_selection = latest
            previous = _DepthCycle(
                old_slot, datetime.fromisoformat(end), None if expected is None else tuple(json.loads(expected)),
                tuple(json.loads(targets)), coverage is not None, prior_selection == PROSPECTIVE_SELECTION,
            )
            if now < previous.ends_at:
                return previous, None
            if not previous.closed:
                _close_cycle(connection, previous, expired=True)
            if previous.ends_at < start:
                gap_from = end
        ends_at = start + timedelta(seconds=config.interval_seconds)
        selection, selected, receipt, sha = _depth_selection(
            connection, start, ends_at, config.exchange_info_max_age_seconds,
        )
        targets = tuple(sorted(set(config.symbols).union(selected or ())))
        cycle = _DepthCycle(slot, ends_at, selected, targets, uses_holdout_snapshot=selection == PROSPECTIVE_SELECTION)
        connection.execute(
            """INSERT INTO capture_depth_cycles
               (slot, ends_at, gap_from, exchange_info_received_at, exchange_info_sha256,
                selection, expected_symbols, targets) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (slot, utc_iso(cycle.ends_at), gap_from, receipt, sha, selection,
             None if selected is None else json.dumps(selected), json.dumps(targets)),
        )
    return cycle, None if selected is not None else selection


def _reserve_request(
    connection: sqlite3.Connection, clock: Clock, pace: Callable[[float], None],
    weight: int, budget: int, deadline: datetime,
) -> datetime | None:
    now = clock.now()
    recent = connection.execute(
        """SELECT requested_at, weight FROM capture_request_budget WHERE requested_at > ?
           ORDER BY requested_at, id""",
        (utc_iso(now - MINUTE),),
    ).fetchall()
    ready = now
    if recent:
        last, last_weight = recent[-1]
        spacing = timedelta(microseconds=ceil(60_000_000 * last_weight / budget))
        ready = max(ready, datetime.fromisoformat(last) + spacing)
    used = sum(cost for _, cost in recent)
    for stamp, cost in recent:
        if used + weight <= budget:
            break
        ready = max(ready, datetime.fromisoformat(stamp) + MINUTE)
        used -= cost
    if ready >= deadline:
        return None
    if ready > now:
        pace((ready - now).total_seconds())
    now = clock.now()
    if not ready <= now < deadline:
        return None
    # Reserve before I/O so a lost response or a process restart still consumes budget.
    with connection:
        connection.execute(
            "DELETE FROM capture_request_budget WHERE requested_at <= ?", (utc_iso(now - MINUTE),),
        )
        connection.execute(
            "INSERT INTO capture_request_budget (requested_at, weight) VALUES (?, ?)", (utc_iso(now), weight),
        )
    return now


def capture_due(
    connection: sqlite3.Connection,
    config: CaptureConfig,
    clock: Clock,
    fetch: Callable[[str], PublicResponse],
    pace: Callable[[float], None],
) -> list[str]:
    if restriction_open(connection):
        return [BLOCKED]
    outcomes: list[str] = []
    cycle = None
    symbols = config.symbols
    if config.depth_targets == DepthTargets.TRADING_USDT_PERPETUALS:
        now = clock.now()
        cycle, fallback = _depth_cycle(connection, config, now)
        if now < datetime.fromisoformat(cycle.slot):
            return ["DEFERRED_CLOCK_BEFORE_CAPTURE_SLOT"]
        symbols = cycle.targets if not cycle.closed else ()
        if fallback is not None:
            outcomes.append(f"DEPTH_FALLBACK_{fallback}")
    for endpoint, url, period in _targets(config, symbols):
        start = clock.now()
        started = utc_iso(start)
        if connection.execute(
            """SELECT 1 FROM capture_attempts WHERE status = 'RATE_LIMITED'
               AND retry_at > ? LIMIT 1""",
            (started,),
        ).fetchone():
            outcomes.append("DEFERRED_RATE_LIMIT")
            break
        slot = cycle.slot if cycle is not None and period != DAILY else _slot(start, period)
        previous = connection.execute(
            """SELECT status, retry_at FROM capture_attempts
               WHERE source_url = ? AND slot = ?
               ORDER BY (status = 'CAPTURED') DESC, id DESC LIMIT 1""",
            (url, slot),
        ).fetchone()
        if previous is not None:
            if previous[0] == "CAPTURED":
                continue
            if previous[1] and previous[1] > started:
                outcomes.append("DEFERRED_RETRY")
                continue

        if cycle is not None:
            deadline = cycle.ends_at
            if period == DAILY:
                deadline = min(deadline, datetime.fromisoformat(slot) + timedelta(days=1))
            reserved = _reserve_request(
                connection, clock, pace, REQUEST_WEIGHT[endpoint], config.request_weight_per_minute, deadline,
            )
            if reserved is None:
                outcomes.append("DEFERRED_CAPTURE_WINDOW")
                break
            start = reserved
            started = utc_iso(start)
        response = fetch(url)
        end = clock.now()
        status = _status(endpoint, response)
        if (
            status == "CAPTURED" and endpoint == "depth" and cycle is not None
            and cycle.uses_holdout_snapshot and end >= cycle.ends_at
        ):
            # A holdout-derived plan must not produce usable payloads after its slot.
            status = "RECEIVED_AFTER_SLOT"
        retry_at = None
        payload = digest = provenance = encoding = None
        if status == "CAPTURED":
            payload = zlib.compress(response.payload)
            digest = hashlib.sha256(response.payload).hexdigest()
            provenance = "VERIFIED"
            encoding = PAYLOAD_ENCODING
        elif status == "RATE_LIMITED":
            delay = retry_delay(response.retry_after, end.timestamp(), 300)
            retry_at = utc_iso(end + timedelta(seconds=min(delay, 31_536_000)))
        elif status not in {RESTRICTED, "RECEIVED_AFTER_SLOT"}:
            retry_at = utc_iso(end + timedelta(seconds=60))

        with connection:
            connection.execute(
                """INSERT INTO capture_attempts
                   (source_url, slot, requested_at, received_at, status, http_status,
                    retry_at, payload, sha256, provenance, payload_encoding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                (
                    url,
                    slot,
                    started,
                    utc_iso(end),
                    status,
                    response.status or None,
                    retry_at,
                    payload,
                    digest,
                    provenance,
                    encoding,
                ),
            )
        outcomes.append(status)
        if status in {"RATE_LIMITED", RESTRICTED}:
            break
        if cycle is None:
            pace(1)
    if cycle is not None and not cycle.closed:
        with connection:
            _close_cycle(connection, cycle, expired=clock.now() >= cycle.ends_at)
    return outcomes


def _status(endpoint: str, response: PublicResponse) -> str:
    match response.status:
        case 200:
            return (
                "CAPTURED"
                if valid_payload(endpoint, response.payload)
                else "INVALID_PAYLOAD"
            )
        case 403 | 451:
            return RESTRICTED
        case 418 | 429:
            return "RATE_LIMITED"
        case 0:
            return "NETWORK_ERROR"
        case _:
            return "HTTP_ERROR"
