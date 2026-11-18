import re
import tomllib
from pathlib import Path
from typing import Any

from acgs.config import CaptureConfig, Config, ConfigError, DepthTargets, Mode


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
        "depth_targets",
        "exchange_info_max_age_seconds",
        "request_weight_per_minute",
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
    try:
        targets = DepthTargets(raw.get("depth_targets", DepthTargets.CONFIGURED))
    except ValueError:
        raise ConfigError("Invalid capture depth target mode") from None
    settings = {"exchange_info_max_age_seconds", "request_weight_per_minute"}
    if targets is DepthTargets.CONFIGURED and settings & raw.keys():
        raise ConfigError("Depth freshness and request budget apply only to dynamic capture")
    if targets is DepthTargets.TRADING_USDT_PERPETUALS and not settings <= raw.keys():
        raise ConfigError("Dynamic capture requires an explicit freshness and request weight budget")
    return CaptureConfig(
        tuple(symbols), interval, targets,
        raw.get("exchange_info_max_age_seconds", CaptureConfig.exchange_info_max_age_seconds),
        raw.get("request_weight_per_minute", CaptureConfig.request_weight_per_minute),
    )
