import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from acgs.__main__ import main
from acgs.capture import (
    BLOCKED,
    RESTRICTED,
    capture_due,
    clear_access_restriction,
    stored_payload,
)
from acgs.clock import FixedClock, utc_iso
from acgs.config import CaptureConfig, Config
from acgs.ledger import open_ledger
from acgs.market_data import PublicResponse

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"
# Byte hashes of the committed public-response excerpts (LF line endings).
FIXTURE_SHA256 = {
    "depth.json": "3911bf965523835a52152dfe7b7c92674045d1eb5e45b459a73d2ac5973fcb92",
    "exchangeInfo.json": "b645121e3cd69e3fcabb2113e93787b568bea3e15875d0695f95f56ae65d7905",
    "fundingInfo.json": "3563587231f6224b208dc843c2a7b79a52a6889b466ad9a3653b7b0bd05bdd19",
    "premiumIndex.json": "9d551d0572328476da297b09df32bf7255398abe752cebfe5176a5bada3c5c81",
}


def fixture_response(url: str) -> PublicResponse:
    path = urlsplit(url).path
    return PublicResponse(
        200, (FIXTURES / f"{path.rsplit('/', 1)[-1]}.json").read_bytes()
    )


def no_wait(seconds: float) -> None:
    pass


def test_fixture_bytes_match_pinned_hashes() -> None:
    assert {path.name for path in FIXTURES.glob("*.json")} == FIXTURE_SHA256.keys()
    for name, expected in FIXTURE_SHA256.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == expected


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
            """SELECT source_url, requested_at, received_at, payload, payload_encoding,
                      sha256, provenance
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
    for index, (url, requested, received, payload, encoding, digest, tag) in enumerate(
        rows
    ):
        raw = fixture_response(url).payload
        assert url.startswith("https://fapi.binance.com/fapi/v1/")
        assert requested == received == utc_iso(NOW if index < 4 else later)
        assert encoding == "zlib" and payload != raw
        assert stored_payload(payload, encoding) == raw
        assert digest == FIXTURE_SHA256[f"{urlsplit(url).path.rsplit('/', 1)[-1]}.json"]
        assert tag == "VERIFIED"


@pytest.mark.parametrize("http_status", [403, 451])
def test_access_restriction_latches_until_operator_clears_it(
    tmp_path: Path, http_status: int
) -> None:
    calls: list[str] = []

    def restricted(url: str) -> PublicResponse:
        calls.append(url)
        return PublicResponse(http_status, b"untrusted response text")

    def counted(url: str) -> PublicResponse:
        calls.append(url)
        return fixture_response(url)

    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        assert capture_due(
            connection, CaptureConfig(), FixedClock(NOW), restricted, no_wait
        ) == [RESTRICTED]
        assert connection.execute(
            "SELECT http_status, payload, provenance, cleared_at FROM capture_attempts"
        ).fetchall() == [(http_status, None, None, None)]
        later = FixedClock(NOW + timedelta(days=2))
        assert capture_due(connection, CaptureConfig(), later, counted, no_wait) == [
            BLOCKED
        ]
        assert len(calls) == 1
        assert clear_access_restriction(connection, Config(), later) == 1
        assert connection.execute(
            "SELECT kind, event_time FROM state_events"
        ).fetchall() == [("CAPTURE_RESTRICTION_CLEARED", utc_iso(later.now()))]
        assert clear_access_restriction(connection, Config(), later) == 0
        assert capture_due(connection, CaptureConfig(), later, counted, no_wait) == [
            "CAPTURED"
        ] * 5


@pytest.mark.parametrize(
    ("response", "outcome", "retry_seconds"),
    [
        (PublicResponse(500), "HTTP_ERROR", 60),
        (PublicResponse(302), "HTTP_ERROR", 60),
        (PublicResponse(0), "NETWORK_ERROR", 60),
        (PublicResponse(200, b"not json"), "INVALID_PAYLOAD", 60),
        (PublicResponse(418, retry_after="120"), "RATE_LIMITED", 300),
    ],
)
def test_failed_responses_are_recorded_without_provenance(
    tmp_path: Path, response: PublicResponse, outcome: str, retry_seconds: int
) -> None:
    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        outcomes = capture_due(
            connection,
            CaptureConfig(("BTCUSDT",), 300),
            FixedClock(NOW),
            lambda url: response,
            no_wait,
        )
        row = connection.execute(
            """SELECT status, http_status, retry_at, payload, sha256, provenance
               FROM capture_attempts ORDER BY id LIMIT 1"""
        ).fetchone()
    assert outcomes[0] == outcome
    assert row == (
        outcome,
        response.status or None,
        utc_iso(NOW + timedelta(seconds=retry_seconds)),
        None,
        None,
        None,
    )


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


def test_non_utc_clocks_keep_retry_order(tmp_path: Path) -> None:
    jakarta = timezone(timedelta(hours=7))

    def throttled(url: str) -> PublicResponse:
        return PublicResponse(429, retry_after="600")

    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        assert capture_due(
            connection, CaptureConfig(), FixedClock(NOW), throttled, no_wait
        ) == ["RATE_LIMITED"]
        local_nine_minutes = FixedClock((NOW + timedelta(minutes=9)).astimezone(jakarta))
        assert capture_due(
            connection, CaptureConfig(), local_nine_minutes, fixture_response, no_wait
        ) == ["DEFERRED_RATE_LIMIT"]
        local_ten_minutes = FixedClock((NOW + timedelta(minutes=10)).astimezone(jakarta))
        assert capture_due(
            connection, CaptureConfig(), local_ten_minutes, fixture_response, no_wait
        ) == ["CAPTURED"] * 5


def test_naive_clock_is_refused_before_any_request(tmp_path: Path) -> None:
    def unexpected(url: str) -> PublicResponse:
        pytest.fail("A request was sent with a naive clock")

    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        with pytest.raises(ValueError):
            capture_due(
                connection,
                CaptureConfig(),
                FixedClock(datetime(2026, 1, 1)),
                unexpected,
                no_wait,
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


def test_capture_cli_exit_codes_and_restriction_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "capture.sqlite3")
    calls: list[str] = []
    responses = {"mode": "restricted"}

    def fake_fetch(url: str) -> PublicResponse:
        calls.append(url)
        if responses["mode"] == "restricted":
            return PublicResponse(451)
        return fixture_response(url)

    monkeypatch.setattr("acgs.__main__.fetch_public", fake_fetch)
    monkeypatch.setattr("acgs.__main__.SystemClock", lambda: FixedClock(NOW))
    monkeypatch.setattr("time.sleep", no_wait)

    assert main(["--db", db, "capture"]) == 1
    assert "Operator review required" in capsys.readouterr().err
    assert main(["--db", db, "capture"]) == 1
    assert len(calls) == 1
    assert main(["--db", db, "capture", "--clear-restriction"]) == 0
    assert "Cleared 1" in capsys.readouterr().out
    responses["mode"] = "ok"
    assert main(["--db", db, "capture"]) == 0
    assert main(["--db", db, "capture"]) == 0
    assert len(calls) == 6
    with pytest.raises(SystemExit):
        main(["--db", db, "capture", "--watch", "--clear-restriction"])


def test_capture_cli_watch_polls_until_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def sleep(seconds: float) -> None:
        if seconds == 30:
            raise KeyboardInterrupt

    monkeypatch.setattr("acgs.__main__.fetch_public", fixture_response)
    monkeypatch.setattr("acgs.__main__.SystemClock", lambda: FixedClock(NOW))
    monkeypatch.setattr("time.sleep", sleep)
    db = tmp_path / "capture.sqlite3"
    assert main(["--db", str(db), "capture", "--watch"]) == 130
    with open_ledger(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM capture_attempts WHERE status = 'CAPTURED'"
        ).fetchone() == (5,)
