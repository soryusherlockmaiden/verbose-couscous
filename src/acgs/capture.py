import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from acgs.clock import Clock
from acgs.config import CaptureConfig
from acgs.market_data import BASE_URL, PublicResponse, retry_delay, valid_payload

DAILY = 86_400


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


def capture_due(
    connection: sqlite3.Connection,
    config: CaptureConfig,
    clock: Clock,
    fetch: Callable[[str], PublicResponse],
    pace: Callable[[float], None],
) -> list[str]:
    outcomes: list[str] = []
    for endpoint, url, period in _targets(config):
        start = clock.now()
        if connection.execute(
            """SELECT 1 FROM capture_attempts WHERE status = 'RATE_LIMITED'
               AND retry_at > ? LIMIT 1""",
            (start.isoformat(),),
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
            if previous[1] and previous[1] > start.isoformat():
                outcomes.append("DEFERRED_RETRY")
                continue

        response = fetch(url)
        end = clock.now()
        status = _status(endpoint, response)
        retry_at = None
        payload = digest = provenance = None
        if status == "CAPTURED":
            payload = response.payload
            digest = hashlib.sha256(payload).hexdigest()
            provenance = "VERIFIED"
        elif status == "RATE_LIMITED":
            delay = retry_delay(response.retry_after, end.timestamp(), 300)
            retry_at = (end + timedelta(seconds=min(delay, 31_536_000))).isoformat()
        elif status != "ACCESS_RESTRICTED":
            retry_at = (end + timedelta(seconds=60)).isoformat()

        with connection:
            connection.execute(
                """INSERT INTO capture_attempts
                   (source_url, slot, requested_at, received_at, status, http_status,
                    retry_at, payload, sha256, provenance)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                (
                    url,
                    slot,
                    start.isoformat(),
                    end.isoformat(),
                    status,
                    response.status or None,
                    retry_at,
                    payload,
                    digest,
                    provenance,
                ),
            )
        outcomes.append(status)
        if status in {"RATE_LIMITED", "ACCESS_RESTRICTED"}:
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
            return "ACCESS_RESTRICTED"
        case 418 | 429:
            return "RATE_LIMITED"
        case 0:
            return "NETWORK_ERROR"
        case _:
            return "HTTP_ERROR"
