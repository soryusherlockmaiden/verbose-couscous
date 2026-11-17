import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from acgs.__main__ import main
from acgs.clock import FixedClock, utc_iso
from acgs.config import Config, Mode
from acgs.config_io import load_config
from acgs.ledger import MIGRATIONS, SCHEMA_VERSION, open_ledger
from acgs.smoke import run_smoke


def test_offline_default_and_config_digest(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'mode = "OFFLINE"\n[capture]\ninterval_seconds = 300\n', encoding="utf-8"
    )
    assert load_config(None).mode is Mode.OFFLINE
    assert load_config(config).digest() == Config().digest()


@pytest.mark.parametrize(
    "content",
    [
        'mode = "PAPER"',
        'mode = "LIVE"',
        'mode = "LIVE_SHADOW"',
        'mode = "LIVE_ARMED"',
        'mode = "test-value-that-must-stay-private"',
        'secret_key = "test-value-that-must-stay-private"',
        'mode = "test-value-that-must-stay-private',
        "mode = {}",
        "[capture]\ninterval_seconds = true",
        "[capture]\ninterval_seconds = 0",
        '[capture]\nsymbols = ["BTCUSDT", "BTCUSDT"]',
        '[capture]\nsymbols = ["BTC/USDT"]',
    ],
)
def test_invalid_config_is_refused_before_side_effects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(content, encoding="utf-8")
    db = tmp_path / "no.sqlite3"
    assert main(["--config", str(config), "--db", str(db), "capture"]) == 2
    assert not db.exists()
    output = capsys.readouterr()
    assert output.err.startswith("Configuration refused:")
    assert "test-value-that-must-stay-private" not in output.err + output.out


def test_smoke_uses_injected_clock_and_replays_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "smoke.sqlite3"
    clock = FixedClock(datetime(2000, 1, 1, tzinfo=UTC))
    with open_ledger(path) as connection:
        assert run_smoke(connection, Config(), clock)
    with open_ledger(path) as connection:
        assert not run_smoke(connection, Config(), clock)
        assert connection.execute(
            "SELECT event_time, kind FROM state_events"
        ).fetchall() == [(utc_iso(clock.now()), "OFFLINE_SMOKE")]
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_smoke_does_not_call_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_fetch(url: str) -> None:
        pytest.fail("The offline smoke command attempted a network request")

    monkeypatch.setattr("acgs.__main__.fetch_public", unexpected_fetch)
    assert main(["--db", str(tmp_path / "smoke.sqlite3"), "smoke"]) == 0


def test_smoke_replay_detects_a_changed_state_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "smoke.sqlite3"
    assert main(["--db", str(db), "smoke"]) == 0
    assert main(["--db", str(db), "smoke"]) == 0
    assert "replayed" in capsys.readouterr().out
    with open_ledger(db) as connection, connection:
        connection.execute("UPDATE state_events SET event_time = '2000-01-01T00:00:00+00:00'")
    assert main(["--db", str(db), "smoke"]) == 1
    assert capsys.readouterr().err.startswith("Ledger state check failed:")


def test_version_1_ledger_migrates_and_keeps_its_records(tmp_path: Path) -> None:
    db = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(db)
    with connection:
        for statement in MIGRATIONS[0]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO config_versions VALUES (?, ?)",
            (Config().digest(), Config().canonical_json()),
        )
        connection.execute(
            "INSERT INTO state_events VALUES (?, ?, ?, 'OFFLINE_SMOKE')",
            (f"offline-smoke:{Config().digest()}", Config().digest(), "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO capture_attempts (source_url, slot, requested_at, received_at,
               status, http_status, payload, sha256, provenance)
               VALUES ('u', 's', 'r', 'r', 'CAPTURED', 200, x'7b7d', 'h', 'VERIFIED')"""
        )
    connection.close()
    with open_ledger(db) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert migrated.execute(
            "SELECT payload, payload_encoding, cleared_at FROM capture_attempts"
        ).fetchall() == [(b"{}", None, None)]
    assert main(["--db", str(db), "smoke"]) == 0


def test_newer_ledger_schema_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()
    assert main(["--db", str(db), "smoke"]) == 1
