import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Mode(StrEnum):
    OFFLINE = "OFFLINE"
    PAPER = "PAPER"
    LIVE = "LIVE"
    LIVE_SHADOW = "LIVE_SHADOW"
    LIVE_ARMED = "LIVE_ARMED"


class ConfigError(ValueError):
    """Configuration error with a message safe for logs."""


@dataclass(frozen=True)
class CaptureConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    interval_seconds: int = 300


@dataclass(frozen=True)
class Config:
    mode: Mode = Mode.OFFLINE
    capture: CaptureConfig = CaptureConfig()

    def __post_init__(self) -> None:
        if self.mode is not Mode.OFFLINE:
            raise ConfigError("M0 supports only OFFLINE mode")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigError("Cannot read configuration as UTF-8 TOML") from None
    if raw.keys() - {"mode", "capture"}:
        raise ConfigError("Unknown configuration field")
    try:
        mode = Mode(raw.get("mode", Mode.OFFLINE))
    except ValueError:
        raise ConfigError("Invalid execution mode") from None
    capture = raw.get("capture", {})
    if not isinstance(capture, dict) or capture.keys() - {
        "symbols",
        "interval_seconds",
    }:
        raise ConfigError("Invalid capture configuration fields")
    return Config(mode, _capture_config(capture))


def _capture_config(raw: dict[str, Any]) -> CaptureConfig:
    symbols = raw.get("symbols", list(CaptureConfig.symbols))
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 30:
        raise ConfigError("Capture symbols must contain 1 to 30 USDT symbols")
    if any(
        not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,24}USDT", symbol)
        for symbol in symbols
    ) or len(set(symbols)) != len(symbols):
        raise ConfigError("Capture symbols must be unique uppercase USDT symbols")
    interval = raw.get("interval_seconds", CaptureConfig.interval_seconds)
    if type(interval) is not int or not 60 <= interval <= 3600:
        raise ConfigError("Capture interval must be an integer from 60 to 3600 seconds")
    return CaptureConfig(tuple(symbols), interval)
