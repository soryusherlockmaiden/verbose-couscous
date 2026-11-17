from datetime import UTC, datetime
from pathlib import Path

import pytest

from acgs.__main__ import main
from acgs.clock import FixedClock
from acgs.config import Config, Mode, load_config
from acgs.ledger import open_ledger
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
        ).fetchall() == [(clock.now().isoformat(), "OFFLINE_SMOKE")]
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)


def test_smoke_does_not_call_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_fetch(url: str) -> None:
        pytest.fail("The offline smoke command attempted a network request")

    monkeypatch.setattr("acgs.__main__.fetch_public", unexpected_fetch)
    assert main(["--db", str(tmp_path / "smoke.sqlite3"), "smoke"]) == 0
