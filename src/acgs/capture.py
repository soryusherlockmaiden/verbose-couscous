import hashlib
import sqlite3
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from acgs.clock import Clock, utc_iso
from acgs.config import CaptureConfig, Config
from acgs.ledger import insert_state_event
from acgs.market_data import BASE_URL, PublicResponse, retry_delay, valid_payload

DAILY = 86_400
PAYLOAD_ENCODING = "zlib"
RESTRICTED = "ACCESS_RESTRICTED"
BLOCKED = "BLOCKED_ACCESS_RESTRICTED"


def _targets(config: CaptureConfig) -> Iterator[tuple[str, str, int]]:
    yield "exchangeInfo", f"{BASE_URL}/fapi/v1/exchangeInfo", DAILY
    yield "fundingInfo", f"{BASE_URL}/fapi/v1/fundingInfo", DAILY
    yield "premiumIndex", f"{BASE_URL}/fapi/v1/premiumIndex", config.interval_seconds
    for symbol in config.symbols:
        query = urlencode({"symbol": symbol, "limit": 5})
        yield "depth", f"{BASE_URL}/fapi/v1/depth?{query}", config.interval_seconds


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
    for endpoint, url, period in _targets(config):
        start = clock.now()
        started = utc_iso(start)
        if connection.execute(
            """SELECT 1 FROM capture_attempts WHERE status = 'RATE_LIMITED'
               AND retry_at > ? LIMIT 1""",
            (started,),
        ).fetchone():
            outcomes.append("DEFERRED_RATE_LIMIT")
            break
        slot = _slot(start, period)
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

        response = fetch(url)
        end = clock.now()
        status = _status(endpoint, response)
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
        elif status != RESTRICTED:
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
        pace(1)
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
