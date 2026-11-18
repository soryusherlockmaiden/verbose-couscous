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


class DepthTargets(StrEnum):
    CONFIGURED = "configured"
    TRADING_USDT_PERPETUALS = "trading_usdt_perpetuals"


MAX_CAPTURE_WEIGHT_PER_MINUTE = 2_400 // 2
EXCHANGE_INFO_MAX_AGE_SECONDS = 129_600


@dataclass(frozen=True)
class CaptureConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    interval_seconds: int = 300
    depth_targets: DepthTargets = DepthTargets.CONFIGURED
    exchange_info_max_age_seconds: int = EXCHANGE_INFO_MAX_AGE_SECONDS
    request_weight_per_minute: int = MAX_CAPTURE_WEIGHT_PER_MINUTE

    def __post_init__(self) -> None:
        if type(self.exchange_info_max_age_seconds) is not int or self.exchange_info_max_age_seconds < 1:
            raise ConfigError("Exchange information freshness must be a positive integer in seconds")
        if type(self.request_weight_per_minute) is not int or not (
            10 <= self.request_weight_per_minute <= MAX_CAPTURE_WEIGHT_PER_MINUTE
        ):
            raise ConfigError(
                f"Capture request weight budget must be an integer from 10 to {MAX_CAPTURE_WEIGHT_PER_MINUTE} per minute"
            )


@dataclass(frozen=True)
class Config:
    mode: Mode = Mode.OFFLINE
    capture: CaptureConfig = CaptureConfig()

    def __post_init__(self) -> None:
        if self.mode is not Mode.OFFLINE:
            raise ConfigError("M0 supports only OFFLINE mode")

    def canonical_json(self) -> str:
        record = asdict(self)
        if self.capture.depth_targets == DepthTargets.CONFIGURED:
            for name in ("depth_targets", "exchange_info_max_age_seconds", "request_weight_per_minute"):
                del record["capture"][name]
        return json.dumps(record, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
