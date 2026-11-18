"""Versioned OFFLINE assumptions shared by the reports and broker."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import StrEnum
from typing import Any

from acgs.pit import DAY, HOUR, ZERO


class StopBasis(StrEnum):
    MARK_PRICE = "MARK_PRICE"
    CONTRACT_PRICE = "CONTRACT_PRICE"


@dataclass(frozen=True)
class Policy:
    version: str
    stop_basis: StopBasis
    allow_documented_mark_proxy: bool
    metadata_age_ms: int
    history_days: int
    volume_days: int
    momentum_days: int
    top_liquid: int
    funding_intervals_ms: tuple[int, ...]
    funding_jitter_ms: int
    funding_ownership_window_ms: int
    taker_fee: Decimal
    slippage: Decimal
    stressed_slippage: Decimal
    funding_reserve_rate: Decimal
    funding_reserve_horizon_ms: int
    fixed_reserve: Decimal
    margin_utilization: Decimal
    liquidation_buffer_rate: Decimal
    liquidation_fee: Decimal
    fill_fraction: Decimal
    ign_risks: tuple[Decimal, ...]
    growth_risks: tuple[Decimal, ...]
    growth_equities: tuple[Decimal, ...]
    stop_factors: tuple[Decimal, ...]
    cost_factors: tuple[Decimal, ...]
    ignition_leverage: int
    growth_leverage: int
    atr_multiple: Decimal
    exposure_cap: Decimal
    halt_buffer: Decimal
    accounting_tolerance: Decimal
    decimal_precision: int
    allow_reconstructed: bool

    def __post_init__(self) -> None:
        counts = (
            self.metadata_age_ms, self.history_days, self.volume_days, self.momentum_days,
            self.top_liquid, self.ignition_leverage, self.growth_leverage,
            self.funding_reserve_horizon_ms,
        )
        if (
            self.version != "m1-v3"
            or min(counts) <= 0 or self.history_days <= self.momentum_days
            or self.history_days < max(15, self.volume_days)
            or not 28 <= self.decimal_precision <= 50
            or not 0 <= self.funding_jitter_ms < HOUR // 2
            or not 0 <= self.funding_ownership_window_ms < HOUR // 2
            or not self.funding_intervals_ms
            or tuple(sorted(set(self.funding_intervals_ms))) != self.funding_intervals_ms
            or min(self.funding_intervals_ms) < HOUR
        ):
            raise ValueError("Invalid M1 policy identity, units or lookbacks")
        fractions = (
            self.taker_fee, self.slippage, self.stressed_slippage, self.funding_reserve_rate,
            self.margin_utilization, self.liquidation_buffer_rate, self.liquidation_fee,
            self.fill_fraction, *self.ign_risks, *self.growth_risks,
        )
        if any(not x.is_finite() or not ZERO <= x <= 1 for x in fractions):
            raise ValueError("Invalid M1 policy fraction")
        if not (
            ZERO < self.margin_utilization <= 1 and ZERO < self.fill_fraction <= 1
            and self.slippage <= self.stressed_slippage < 1
            and all(self.ign_risks) and self.ign_risks
            and all(self.growth_risks) and self.growth_risks
        ):
            raise ValueError("Invalid M1 sizing or execution assumption")
        positives = (
            self.atr_multiple, self.exposure_cap, self.accounting_tolerance,
            *self.stop_factors, *self.cost_factors, *self.growth_equities,
        )
        if (
            not self.stop_factors or not self.cost_factors or not self.growth_equities
            or any(not x.is_finite() or x <= ZERO for x in positives)
            or min(self.growth_equities) != 50
            or any(not x.is_finite() or x < ZERO for x in (self.fixed_reserve, self.halt_buffer))
        ):
            raise ValueError("Invalid M1 reserves, scenario grid or tolerance")

    def context(self) -> Context:
        return Context(prec=self.decimal_precision, rounding=ROUND_HALF_EVEN)

    @property
    def digest(self) -> str:
        return digest(asdict(self))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def instant_ms(instant: datetime) -> int:
    # Avoid floating-point timestamps in decision keys.
    if instant.utcoffset() is None:
        raise ValueError("Replay requires a timezone-aware clock")
    delta = instant - EPOCH
    if delta.microseconds % 1000:
        raise ValueError("Replay clock resolution is milliseconds")
    return delta.days * DAY + delta.seconds * 1000 + delta.microseconds // 1000
