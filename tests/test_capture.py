import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from acgs.capture import capture_due
from acgs.clock import FixedClock
from acgs.config import CaptureConfig
from acgs.ledger import open_ledger
from acgs.market_data import PublicResponse

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


def fixture_response(url: str) -> PublicResponse:
    path = urlsplit(url).path
    return PublicResponse(
        200, (FIXTURES / f"{path.rsplit('/', 1)[-1]}.json").read_bytes()
    )


def no_wait(seconds: float) -> None:
    pass


def test_public_capture_provenance_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    config = CaptureConfig(("BTCUSDT",), 300)
    with open_ledger(path) as connection:
        assert (
            capture_due(connection, config, FixedClock(NOW), fixture_response, no_wait)
            == ["CAPTURED"] * 4
        )
    with open_ledger(path) as connection:
        assert not capture_due(
            connection, config, FixedClock(NOW), fixture_response, no_wait
        )
        later = NOW + timedelta(minutes=5)
        assert (
            capture_due(
                connection, config, FixedClock(later), fixture_response, no_wait
            )
            == ["CAPTURED"] * 2
        )
        rows = connection.execute(
            """SELECT source_url, requested_at, received_at, payload, sha256, provenance
               FROM capture_attempts ORDER BY id"""
        ).fetchall()
    assert len(rows) == 6
    assert [urlsplit(row[0]).path for row in rows] == [
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/fundingInfo",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/depth",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/depth",
    ]
    for index, (url, requested, received, payload, digest, tag) in enumerate(rows):
        assert url.startswith("https://fapi.binance.com/fapi/v1/")
        assert requested == received == (NOW if index < 4 else later).isoformat()
        assert payload == fixture_response(url).payload
        assert digest == hashlib.sha256(payload).hexdigest()
        assert tag == "VERIFIED"


@pytest.mark.parametrize("http_status", [403, 451])
def test_access_restriction_records_failure_and_stops(
    tmp_path: Path, http_status: int
) -> None:
    def restricted(url: str) -> PublicResponse:
        return PublicResponse(http_status, b"untrusted response text")

    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        assert capture_due(
            connection, CaptureConfig(), FixedClock(NOW), restricted, no_wait
        ) == ["ACCESS_RESTRICTED"]
        assert connection.execute(
            "SELECT http_status, payload, provenance FROM capture_attempts"
        ).fetchall() == [(http_status, None, None)]


def test_retry_after_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"

    def throttled(url: str) -> PublicResponse:
        return PublicResponse(429, retry_after="600")

    with open_ledger(path) as connection:
        assert capture_due(
            connection, CaptureConfig(), FixedClock(NOW), throttled, no_wait
        ) == ["RATE_LIMITED"]
    with open_ledger(path) as connection:
        still_throttled = FixedClock(NOW + timedelta(minutes=9))
        assert capture_due(
            connection, CaptureConfig(), still_throttled, fixture_response, no_wait
        ) == ["DEFERRED_RATE_LIMIT"]
        after_wait = FixedClock(NOW + timedelta(minutes=10))
        assert (
            capture_due(
                connection, CaptureConfig(), after_wait, fixture_response, no_wait
            )
            == ["CAPTURED"] * 5
        )


def test_invalid_payload_can_be_retried_without_false_provenance(
    tmp_path: Path,
) -> None:
    def malformed(url: str) -> PublicResponse:
        if url.endswith("/exchangeInfo"):
            return PublicResponse(200, b'{"error":"not market data"}')
        return fixture_response(url)

    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        assert capture_due(
            connection, CaptureConfig(), FixedClock(NOW), malformed, no_wait
        ) == ["INVALID_PAYLOAD", "CAPTURED", "CAPTURED", "CAPTURED", "CAPTURED"]
        assert connection.execute(
            "SELECT provenance FROM capture_attempts WHERE status = 'INVALID_PAYLOAD'"
        ).fetchall() == [(None,)]
        early = FixedClock(NOW + timedelta(seconds=59))
        assert capture_due(connection, CaptureConfig(), early, fixture_response, no_wait) == [
            "DEFERRED_RETRY"
        ]
        retry = FixedClock(NOW + timedelta(minutes=1))
        assert capture_due(
            connection, CaptureConfig(), retry, fixture_response, no_wait
        ) == ["CAPTURED"]
