import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


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
