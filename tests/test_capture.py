import hashlib
import json
import sqlite3
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from acgs.__main__ import main
from acgs.capture import (
    BLOCKED,
    REQUEST_WEIGHT,
    RESTRICTED,
    capture_due,
    clear_access_restriction,
    stored_payload,
)
from acgs.clock import FixedClock, utc_iso
from acgs.config import CaptureConfig, Config, ConfigError, DepthTargets
from acgs.config_io import load_config
from acgs.history_io import _capture_observations
from acgs.ledger import open_ledger
from acgs.market_data import PublicResponse
from acgs.sim_config import instant_ms

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PROSPECTIVE_END = NOW + timedelta(days=1)
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


def test_malformed_records_are_retried_instead_of_captured(tmp_path: Path) -> None:
    malformed = {
        "exchangeInfo": b'{"symbols":[null]}',
        "fundingInfo": b'[{"symbol":null,"fundingIntervalHours":8}]',
        "premiumIndex": b'[{"symbol":null,"markPrice":"not-a-price","nextFundingTime":null}]',
        "depth": b'{"lastUpdateId":1,"bids":[null],"asks":[]}',
    }
    calls: list[str] = []

    def broken(url: str) -> PublicResponse:
        calls.append(url)
        return PublicResponse(200, malformed[urlsplit(url).path.rsplit("/", 1)[-1]])

    def recorded(url: str) -> PublicResponse:
        calls.append(url)
        return fixture_response(url)

    config = CaptureConfig(("BTCUSDT",), 300)
    with open_ledger(tmp_path / "capture.sqlite3") as connection:
        assert capture_due(connection, config, FixedClock(NOW), broken, no_wait) == [
            "INVALID_PAYLOAD"
        ] * 4
        assert connection.execute(
            "SELECT COUNT(*) FROM capture_attempts WHERE provenance IS NOT NULL"
        ).fetchone() == (0,)
        retry = FixedClock(NOW + timedelta(seconds=60))
        assert capture_due(connection, config, retry, recorded, no_wait) == ["CAPTURED"] * 4
    assert len(calls) == 8


def test_capture_watch_pauses_on_restriction_and_resumes_after_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "capture.sqlite3"
    calls: list[str] = []
    state = {"restricted": True, "polls": 0}

    def fake_fetch(url: str) -> PublicResponse:
        calls.append(url)
        return PublicResponse(451) if state["restricted"] else fixture_response(url)

    def sleep(seconds: float) -> None:
        if seconds != 30:
            return
        state["polls"] += 1
        if state["polls"] == 3:
            # Two paused polls have passed; the operator reviews and clears the latch.
            state["restricted"] = False
            with open_ledger(db) as operator:
                assert clear_access_restriction(operator, Config(), FixedClock(NOW)) == 1
        elif state["polls"] == 4:
            raise KeyboardInterrupt

    monkeypatch.setattr("acgs.__main__.fetch_public", fake_fetch)
    monkeypatch.setattr("acgs.__main__.SystemClock", lambda: FixedClock(NOW))
    monkeypatch.setattr("time.sleep", sleep)

    assert main(["--db", str(db), "capture", "--watch"]) == 130
    output = capsys.readouterr()
    assert len(calls) == 1 + 5  # one restricted request, none while paused, then resume
    assert output.err.count("Capture is paused") == 1
    assert "BLOCKED_ACCESS_RESTRICTED" not in output.out
    assert "Capture CAPTURED=5" in output.out
    assert "restriction cleared; resuming" in output.out


def test_capture_watch_restarted_while_latched_sends_no_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "capture.sqlite3"
    with open_ledger(db) as connection:
        capture_due(
            connection, CaptureConfig(), FixedClock(NOW), lambda url: PublicResponse(403), no_wait
        )
    polls = {"count": 0}

    def unexpected(url: str) -> PublicResponse:
        pytest.fail("A request was sent while the restriction latch was open")

    def sleep(seconds: float) -> None:
        polls["count"] += 1
        if polls["count"] == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("acgs.__main__.fetch_public", unexpected)
    monkeypatch.setattr("acgs.__main__.SystemClock", lambda: FixedClock(NOW))
    monkeypatch.setattr("time.sleep", sleep)

    # A restart policy restarts the service; it must stay paused rather than exit and loop.
    assert main(["--db", str(db), "capture", "--watch"]) == 130
    output = capsys.readouterr()
    assert output.out.count("BLOCKED_ACCESS_RESTRICTED") == 1
    assert output.err.count("Capture is paused") == 1


DYNAMIC = CaptureConfig(depth_targets=DepthTargets.TRADING_USDT_PERPETUALS)


@dataclass
class AdvancingClock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant

    def advance(self, seconds: float) -> None:
        self.instant += timedelta(seconds=seconds)


def symbol_row(symbol: str, **overrides: str) -> dict[str, object]:
    row = json.loads((FIXTURES / "exchangeInfo.json").read_bytes())["symbols"][0]
    return row | {"symbol": symbol, "status": "TRADING", "quoteAsset": "USDT",
                  "contractType": "PERPETUAL"} | overrides


def fixture_exchange(*symbols: dict[str, object]) -> PublicResponse:
    return PublicResponse(200, json.dumps({"symbols": symbols}).encode())


def fetch_snapshot(url: str, snapshot: PublicResponse) -> PublicResponse:
    return snapshot if urlsplit(url).path.endswith("/exchangeInfo") else fixture_response(url)


def seed_snapshot(path: Path, snapshot: PublicResponse, at: datetime) -> None:
    with open_ledger(path) as connection:
        capture_due(connection, CaptureConfig(("BTCUSDT",)), FixedClock(at),
                    lambda url: fetch_snapshot(url, snapshot), no_wait)


def depth_symbol(url: str) -> str | None:
    parsed = urlsplit(url)
    return parse_qs(parsed.query)["symbol"][0] if parsed.path.endswith("/depth") else None


def test_dynamic_configuration_preserves_legacy_digest(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[capture]\nsymbols = ["BTCUSDT", "ETHUSDT"]\ninterval_seconds = 300\n', encoding="utf-8")
    assert load_config(path).digest() == Config().digest() == (
        "e2fb3f83500b5aa748b93e0e3151cd9fc1536048d56692da249ed0e877362505"
    )
    path.write_text('[capture]\ndepth_targets = "trading_usdt_perpetuals"\n'
                    'exchange_info_max_age_seconds = 129600\nrequest_weight_per_minute = 1200\n',
                    encoding="utf-8")
    assert load_config(path).capture == DYNAMIC
    assert load_config(path).digest() != Config().digest()


@pytest.mark.parametrize("settings", [
    'depth_targets = "all"',
    'depth_targets = "trading_usdt_perpetuals"',
    'depth_targets = "configured"\nrequest_weight_per_minute = 1200',
    'depth_targets = "trading_usdt_perpetuals"\nrequest_weight_per_minute = 1201\nexchange_info_max_age_seconds = 1',
    'depth_targets = "trading_usdt_perpetuals"\nrequest_weight_per_minute = 1200\nexchange_info_max_age_seconds = 0',
])
def test_invalid_dynamic_config_refused(tmp_path: Path, settings: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[capture]\n{settings}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_dynamic_snapshot_selection_and_m1_spread_import(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(
        symbol_row("STOPUSDT", status="SETTLING"),
        symbol_row("ZZZUSDT", underlyingType="INDEX"),
        symbol_row("STOP2USDT", status="CLOSE"),
        symbol_row("DELIVERYUSDT", contractType="CURRENT_QUARTER"),
        symbol_row("ALTUSDC", quoteAsset="USDC"),
        symbol_row("AAAUSDT", underlyingType="PEGGED"),
    ), NOW + timedelta(microseconds=123_456))
    clock = AdvancingClock(NOW + timedelta(minutes=5))
    depth = []

    def fetch(url: str) -> PublicResponse:
        if (symbol := depth_symbol(url)) is not None:
            depth.append(symbol)
        return fixture_response(url)

    with open_ledger(path) as connection:
        assert capture_due(connection, DYNAMIC, clock, fetch, clock.advance) == ["CAPTURED"] * 5
        coverage, = connection.execute("SELECT coverage FROM capture_depth_cycles").fetchone()
        assert json.loads(coverage) == {
            "expected": 2, "captured": 2, "missed": {},
            "configured_only": {"BTCUSDT": "CAPTURED", "ETHUSDT": "CAPTURED"},
        }
    assert depth == ["AAAUSDT", "BTCUSDT", "ETHUSDT", "ZZZUSDT"]
    _, _, _, spreads, _ = _capture_observations(
        path, {"AAAUSDT", "ZZZUSDT"}, instant_ms(NOW + timedelta(minutes=6)),
    )
    assert [spread.instrument.symbol for spread in spreads] == ["AAAUSDT", "ZZZUSDT"]
    assert spreads[0].bps == spreads[1].bps > 0


@pytest.mark.parametrize("scenario", ["MISSING", "STALE", "INVALID", "BAD_HASH", "SEALED"])
def test_dynamic_fallback_is_recorded(tmp_path: Path, scenario: str) -> None:
    path = tmp_path / "capture.sqlite3"
    start = NOW + timedelta(minutes=5)
    if scenario != "MISSING":
        snapshot = fixture_exchange(symbol_row("AAAUSDT"))
        if scenario == "INVALID":
            # Legacy exchangeInfo validation allows absent quoteAsset. A depth plan cannot.
            incomplete = symbol_row("AAAUSDT")
            del incomplete["quoteAsset"]
            snapshot = fixture_exchange(incomplete)
        seed_snapshot(path, snapshot, NOW if scenario != "SEALED" else NOW - timedelta(minutes=1))
        if scenario == "STALE":
            start += timedelta(days=2)
        if scenario == "BAD_HASH":
            with open_ledger(path) as connection, connection:
                connection.execute("UPDATE capture_attempts SET sha256 = '' WHERE source_url LIKE '%/exchangeInfo'")
    clock = AdvancingClock(start)
    reason = ("INVALID" if scenario == "BAD_HASH" else scenario) + "_EXCHANGE_INFO"
    with open_ledger(path) as connection:
        outcomes = capture_due(connection, DYNAMIC, clock, fixture_response, clock.advance)
        assert f"DEPTH_FALLBACK_{reason}" in outcomes
        selection, symbols, targets, coverage = connection.execute(
            "SELECT selection, expected_symbols, targets, coverage FROM capture_depth_cycles",
        ).fetchone()
        assert selection == reason and symbols is None
        assert json.loads(targets) == ["BTCUSDT", "ETHUSDT"]
        assert json.loads(coverage)["expected"] is None


@pytest.fixture
def registered_prospective_holdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    record = tmp_path / "history_seal.json"
    record.write_text(json.dumps({"version": "history-seal-v1", "ranges": [
        {"purpose": "m2-prospective-holdout", "start": utc_iso(NOW),
         "end": utc_iso(PROSPECTIVE_END)},
    ]}), encoding="utf-8")
    monkeypatch.setattr("acgs.history_seal.RECORD_PATH", record)
    return record


def test_prospective_capture_keeps_broad_targets_and_research_skips_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered_prospective_holdout: Path,
) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("ALTUSDT")), NOW + timedelta(seconds=1))
    clock = AdvancingClock(NOW + timedelta(minutes=5))
    with open_ledger(path) as connection:
        assert capture_due(connection, DYNAMIC, clock, fixture_response, clock.advance) == ["CAPTURED"] * 4
        selection, targets, coverage = connection.execute(
            "SELECT selection, targets, coverage FROM capture_depth_cycles",
        ).fetchone()
        assert selection == "PROSPECTIVE_HOLDOUT_EXCHANGE_INFO"
        assert json.loads(targets) == ["ALTUSDT", "BTCUSDT", "ETHUSDT"]
        assert json.loads(coverage)["captured"] == 1
        clock.instant = PROSPECTIVE_END
        assert capture_due(connection, DYNAMIC, clock, fixture_response, clock.advance) == ["CAPTURED"] * 6
        next_selection, next_targets = connection.execute(
            "SELECT selection, targets FROM capture_depth_cycles ORDER BY slot DESC LIMIT 1",
        ).fetchone()
        assert next_selection == "PROSPECTIVE_HOLDOUT_EXCHANGE_INFO"
        assert json.loads(next_targets) == ["ALTUSDT", "BTCUSDT", "ETHUSDT"]
        unsealed_ids = [
            row_id for row_id, in connection.execute(
                "SELECT id FROM capture_attempts WHERE received_at >= ? ORDER BY received_at, id",
                (utc_iso(PROSPECTIVE_END),),
            )
        ]

    queries: list[str] = []
    original_connect = sqlite3.connect

    def traced(database: str, *, uri: bool) -> sqlite3.Connection:
        conn = original_connect(database, uri=uri)
        conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr("acgs.history_io.sqlite3.connect", traced)
    _, _, _, spreads, _ = _capture_observations(
        path, {"ALTUSDT"}, instant_ms(PROSPECTIVE_END + timedelta(minutes=1)),
    )
    assert spreads == []
    assert not any("capture_depth_cycles" in query for query in queries)
    assert [query.rsplit("=", 1)[1].strip() for query in queries if "SELECT payload" in query] == [
        str(row_id) for row_id in unsealed_ids
    ]


def test_prospective_capture_keeps_targets_for_slot_straddling_holdout_end(
    tmp_path: Path, registered_prospective_holdout: Path,
) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("ALTUSDT")), NOW + timedelta(seconds=1))
    clock = AdvancingClock(PROSPECTIVE_END)
    config = replace(DYNAMIC, interval_seconds=420)
    with open_ledger(path) as connection:
        assert capture_due(connection, config, clock, fixture_response, clock.advance) == ["CAPTURED"] * 6
        slot, ends_at, targets = connection.execute(
            "SELECT slot, ends_at, targets FROM capture_depth_cycles",
        ).fetchone()
    assert datetime.fromisoformat(slot) < PROSPECTIVE_END < datetime.fromisoformat(ends_at)
    assert json.loads(targets) == ["ALTUSDT", "BTCUSDT", "ETHUSDT"]


@pytest.mark.parametrize("after_holdout", [False, True])
def test_prospective_plan_does_not_publish_late_depth_payload(
    tmp_path: Path, registered_prospective_holdout: Path, after_holdout: bool,
) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("ALTUSDT")), NOW + timedelta(seconds=1))
    slot_end = PROSPECTIVE_END + timedelta(minutes=5 if after_holdout else 0)
    clock = AdvancingClock(slot_end - timedelta(seconds=2))

    def fetch(url: str) -> PublicResponse:
        if depth_symbol(url) == "ALTUSDT":
            clock.advance(2)
        return fixture_response(url)

    with open_ledger(path) as connection:
        captured_metadata = ["CAPTURED"] * (3 if after_holdout else 1)
        assert capture_due(connection, DYNAMIC, clock, fetch, clock.advance) == (
            captured_metadata + ["RECEIVED_AFTER_SLOT", "DEFERRED_CAPTURE_WINDOW"]
        )
        row = connection.execute(
            "SELECT status, payload, sha256 FROM capture_attempts WHERE source_url LIKE '%symbol=ALTUSDT%'",
        ).fetchone()
        assert row == ("RECEIVED_AFTER_SLOT", None, None)
        coverage, = connection.execute("SELECT coverage FROM capture_depth_cycles").fetchone()
        assert json.loads(coverage)["missed"] == {"ALTUSDT": "RECEIVED_AFTER_SLOT"}


@pytest.mark.parametrize("receipt_in_reserve", [False, True])
def test_dynamic_selection_refuses_retrospective_reserve_before_payload_read(
    tmp_path: Path, registered_prospective_holdout: Path, receipt_in_reserve: bool,
) -> None:
    raw = json.loads(registered_prospective_holdout.read_text(encoding="utf-8"))
    raw["ranges"].append(raw["ranges"][0] | {"purpose": "m2-retrospective-reserve"})
    registered_prospective_holdout.write_text(json.dumps(raw), encoding="utf-8")
    path = tmp_path / "capture.sqlite3"
    stamp = NOW + timedelta(seconds=1 if receipt_in_reserve else -1)
    seed_snapshot(path, fixture_exchange(symbol_row("ALTUSDT")), stamp)
    clock = AdvancingClock(NOW + timedelta(minutes=5))
    queries: list[str] = []
    with open_ledger(path) as connection:
        connection.set_trace_callback(queries.append)
        assert capture_due(connection, DYNAMIC, clock, fixture_response, clock.advance)[0] == (
            "DEPTH_FALLBACK_SEALED_EXCHANGE_INFO"
        )
    assert not any("SELECT payload" in query for query in queries)


def test_restart_keeps_slot_targets_then_new_snapshot_changes_them(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("AAAUSDT"), symbol_row("ZZZUSDT")), NOW)
    start = NOW + timedelta(days=1)
    clock = AdvancingClock(start)
    snapshot = fixture_exchange(symbol_row("NEWUSDT"), symbol_row("ZZZUSDT"))
    attempted = []

    def interrupted(url: str) -> PublicResponse:
        if (symbol := depth_symbol(url)) is not None:
            attempted.append(symbol)
            if symbol == "BTCUSDT":
                raise KeyboardInterrupt
        return fetch_snapshot(url, snapshot)

    with open_ledger(path) as connection, pytest.raises(KeyboardInterrupt):
        capture_due(connection, DYNAMIC, clock, interrupted, clock.advance)
    assert attempted == ["AAAUSDT", "BTCUSDT"]
    resumed = []

    def fetch(url: str) -> PublicResponse:
        if (symbol := depth_symbol(url)) is not None:
            resumed.append(symbol)
        return fixture_response(url)

    with open_ledger(path) as connection:
        capture_due(connection, DYNAMIC, clock, fetch, clock.advance)
        assert resumed == ["BTCUSDT", "ETHUSDT", "ZZZUSDT"]
        assert connection.execute("SELECT coverage IS NOT NULL FROM capture_depth_cycles").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM capture_attempts WHERE source_url LIKE '%symbol=AAAUSDT%'",
        ).fetchone() == (1,)
        resumed.clear()
        clock.instant = start + timedelta(minutes=5)
        capture_due(connection, DYNAMIC, clock, fetch, clock.advance)
        assert resumed == ["BTCUSDT", "ETHUSDT", "NEWUSDT", "ZZZUSDT"]


@pytest.mark.parametrize("budget, latency, max_seconds", [(1200, 0.250, 102), (600, 0.001, 83)])
def test_dynamic_full_cycle_budget_includes_latency_and_all_endpoints(
    tmp_path: Path, budget: int, latency: float, max_seconds: int,
) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(*(symbol_row(f"S{n:03d}USDT") for n in range(400))), NOW)
    start = NOW + timedelta(days=1)
    clock = AdvancingClock(start)
    last_minute: deque[tuple[datetime, int]] = deque()
    captured = []
    peak = 0

    def fetch(url: str) -> PublicResponse:
        nonlocal peak
        endpoint = urlsplit(url).path.rsplit("/", 1)[-1]
        last_minute.append((clock.now(), REQUEST_WEIGHT[endpoint]))
        while last_minute[0][0] <= clock.now() - timedelta(minutes=1):
            last_minute.popleft()
        peak = max(peak, sum(cost for _, cost in last_minute))
        assert peak <= budget
        clock.advance(latency)
        captured.append(endpoint)
        return fixture_response(url)

    with open_ledger(path) as connection:
        config = replace(DYNAMIC, request_weight_per_minute=budget)
        assert capture_due(connection, config, clock, fetch, clock.advance) == ["CAPTURED"] * 405
        coverage, = connection.execute("SELECT coverage FROM capture_depth_cycles").fetchone()
        assert json.loads(coverage)["captured"] == 400
    assert captured[:3] == ["exchangeInfo", "fundingInfo", "premiumIndex"]
    assert captured.count("depth") == 402
    assert peak >= budget // 3
    assert clock.now() - start <= timedelta(seconds=max_seconds)


def test_capture_receipt_after_slot_does_not_count_as_coverage(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("AAAUSDT")), NOW)
    start = NOW + timedelta(minutes=5)
    clock = AdvancingClock(start + timedelta(seconds=298))

    def fetch(url: str) -> PublicResponse:
        if depth_symbol(url) == "AAAUSDT":
            clock.advance(2)
        return fixture_response(url)

    with open_ledger(path) as connection:
        assert capture_due(connection, DYNAMIC, clock, fetch, clock.advance) == [
            "CAPTURED", "CAPTURED", "DEFERRED_CAPTURE_WINDOW",
        ]
        coverage, = connection.execute("SELECT coverage FROM capture_depth_cycles").fetchone()
        assert json.loads(coverage) == {
            "expected": 1, "captured": 0, "missed": {"AAAUSDT": "RECEIVED_AFTER_SLOT"},
            "configured_only": {"BTCUSDT": "UNATTEMPTED", "ETHUSDT": "UNATTEMPTED"},
        }


@pytest.mark.parametrize("http, expected", [(418, "RATE_LIMITED"), (429, "RATE_LIMITED"), (403, RESTRICTED), (451, RESTRICTED)])
def test_dynamic_stop_and_report_expired_gap(tmp_path: Path, http: int, expected: str) -> None:
    path = tmp_path / "capture.sqlite3"
    seed_snapshot(path, fixture_exchange(symbol_row("AAAUSDT"), symbol_row("ZZZUSDT")), NOW)
    start = NOW + timedelta(minutes=5)
    clock = AdvancingClock(start)
    calls = []

    def fetch(url: str) -> PublicResponse:
        calls.append(depth_symbol(url))
        return PublicResponse(http) if calls[-1] == "AAAUSDT" else fixture_response(url)

    with open_ledger(path) as connection:
        assert capture_due(connection, DYNAMIC, clock, fetch, clock.advance) == ["CAPTURED", expected]
    assert calls == [None, "AAAUSDT"]
    with open_ledger(path) as connection:
        blocked = BLOCKED if expected == RESTRICTED else "DEFERRED_RATE_LIMIT"
        assert capture_due(connection, DYNAMIC, clock, fetch, clock.advance) == [blocked]
        assert calls == [None, "AAAUSDT"]
        clock.instant = start + timedelta(minutes=15)
        if expected == RESTRICTED:
            assert clear_access_restriction(connection, Config(capture=DYNAMIC), clock) == 1
        capture_due(connection, DYNAMIC, clock, fixture_response, clock.advance)
        previous, current = connection.execute(
            "SELECT coverage, gap_from FROM capture_depth_cycles ORDER BY slot",
        ).fetchall()
        assert json.loads(previous[0])["missed"] == {"AAAUSDT": expected, "ZZZUSDT": "UNATTEMPTED"}
        assert current[1] == utc_iso(start + timedelta(minutes=5))
