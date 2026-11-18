"""Immutable USD-M market observations and causal views (UTC epoch milliseconds)."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

ZERO = Decimal(0)
DAY = 86_400_000
HOUR = 3_600_000


class Provenance(StrEnum):
    VERIFIED = "VERIFIED"
    RECONSTRUCTED = "RECONSTRUCTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Evidence:
    source: str
    method: str
    provenance: Provenance
    as_of: int
    limitation: str = ""

    def __post_init__(self) -> None:
        if not self.source or not self.method or self.as_of < 0:
            raise ValueError("Evidence requires source, method and UTC observation time")
        if self.provenance != Provenance.VERIFIED and not self.limitation:
            raise ValueError("Uncertain evidence requires a limitation")


@dataclass(frozen=True, order=True)
class Instrument:
    symbol: str
    onboard: int
    multiplier: int = 1

    def __post_init__(self) -> None:
        if not self.symbol.isascii() or not self.symbol.isalnum() or not self.symbol.isupper():
            raise ValueError("Invalid exchange symbol")
        if self.onboard < 0 or self.multiplier < 1:
            raise ValueError("Invalid listing epoch or symbol multiplier")

    @property
    def id(self) -> str:
        return f"{self.symbol}@{self.onboard}"


@dataclass(frozen=True)
class Listing:
    instrument: Instrument
    effective: int
    evidence: Evidence
    status: str
    quote: str = "USDT"
    contract: str = "PERPETUAL"
    excluded_product: bool = False

    @property
    def tradable(self) -> bool:
        return (
            self.status == "TRADING"
            and self.quote == "USDT"
            and self.contract == "PERPETUAL"
            and not self.excluded_product
            and self.evidence.provenance != Provenance.UNAVAILABLE
        )


@dataclass(frozen=True)
class Prices:
    opening: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        values = (self.opening, self.high, self.low, self.close)
        if any(not x.is_finite() or x <= ZERO for x in values):
            raise ValueError("Invalid OHLC price")
        if not self.low <= min(self.opening, self.close) <= max(self.opening, self.close) <= self.high:
            raise ValueError("Inconsistent OHLC extremes")


@dataclass(frozen=True)
class Candle:
    instrument: Instrument
    start: int
    end: int
    available: int
    prices: Prices
    quote_volume: Decimal
    volume: Decimal
    evidence: Evidence
    is_mark: bool = False

    def __post_init__(self) -> None:
        interval = self.end - self.start
        if (
            interval <= 0 or self.start < self.instrument.onboard
            or self.start % interval or self.available < self.end
            or self.available < self.evidence.as_of
        ):
            raise ValueError("Invalid candle boundaries or availability")
        if any(not x.is_finite() or x < ZERO for x in (self.quote_volume, self.volume)):
            raise ValueError("Invalid candle volume")


@dataclass(frozen=True)
class Funding:
    instrument: Instrument
    time: int
    rate: Decimal
    evidence: Evidence
    mark: Decimal | None = None
    interval_ms: int | None = None
    mark_evidence: Evidence | None = None

    def __post_init__(self) -> None:
        if self.time < self.instrument.onboard or self.evidence.as_of < self.time:
            raise ValueError("Invalid funding settlement/availability")
        if self.evidence.provenance != Provenance.VERIFIED:
            raise ValueError("Accounting funding must come from recorded settlements")
        if not self.rate.is_finite() or abs(self.rate) >= 1:
            raise ValueError("Invalid recorded funding rate")
        if self.mark is not None and (not self.mark.is_finite() or self.mark <= ZERO):
            raise ValueError("Invalid funding mark")
        if self.mark_evidence is not None and (
            self.mark is None or self.mark_evidence.provenance != Provenance.VERIFIED
            or self.mark_evidence.as_of < self.time
        ):
            raise ValueError("Invalid funding mark evidence")
        if self.interval_ms is not None and self.interval_ms <= 0:
            raise ValueError("Invalid settlement interval field")


@dataclass(frozen=True)
class Schedule:
    instrument: Instrument
    effective: int
    evidence: Evidence
    interval_ms: int | None = None
    next_time: int | None = None
    cap: Decimal | None = None
    floor: Decimal | None = None
    until: int | None = None
    interval_withdrawn: bool = False

    def __post_init__(self) -> None:
        if self.interval_ms is not None and self.interval_ms <= 0:
            raise ValueError("Invalid funding interval")
        if self.interval_withdrawn and (
            self.interval_ms is not None or self.evidence.provenance != Provenance.UNAVAILABLE
        ):
            raise ValueError("Invalid absent fundingInfo observation")
        if self.next_time is not None and self.next_time <= self.effective:
            raise ValueError("Invalid next funding time")
        if self.until is not None and self.until <= self.effective:
            raise ValueError("Invalid schedule validity")
        if any(x is not None and (not x.is_finite() or abs(x) >= 1)
               for x in (self.cap, self.floor)):
            raise ValueError("Invalid funding cap or floor")
        if self.cap is not None and self.floor is not None and self.cap < self.floor:
            raise ValueError("Inverted funding band")


@dataclass(frozen=True)
class Lot:
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    def __post_init__(self) -> None:
        if (
            any(not x.is_finite() or x <= ZERO for x in (self.minimum, self.maximum, self.step))
            or self.maximum < self.minimum
        ):
            raise ValueError("Invalid lot filter")


@dataclass(frozen=True)
class Filters:
    instrument: Instrument
    effective: int
    evidence: Evidence
    lot: Lot
    market_lot: Lot
    tick: Decimal
    min_notional: Decimal
    min_price: Decimal
    max_price: Decimal
    percent_down: Decimal
    percent_up: Decimal

    def __post_init__(self) -> None:
        values = (self.tick, self.min_notional, self.min_price, self.max_price,
                  self.percent_down, self.percent_up)
        if any(not x.is_finite() or x < ZERO for x in values) or self.tick == ZERO:
            raise ValueError("Invalid price/notional filter")
        if not self.min_price < self.max_price or not ZERO < self.percent_down <= 1 <= self.percent_up:
            raise ValueError("Invalid price bands")


@dataclass(frozen=True)
class FilterWithdrawal:
    """An observation that invalidates earlier filters without carrying their values."""

    instrument: Instrument
    effective: int
    evidence: Evidence

    def __post_init__(self) -> None:
        if self.evidence.provenance != Provenance.UNAVAILABLE:
            raise ValueError("A filter withdrawal requires unavailable evidence")


@dataclass(frozen=True)
class Tier:
    ceiling: Decimal
    maintenance_rate: Decimal
    deduction: Decimal
    max_leverage: int

    def __post_init__(self) -> None:
        if (
            any(not x.is_finite() or x < ZERO
                for x in (self.ceiling, self.maintenance_rate, self.deduction))
            or self.ceiling == ZERO or self.maintenance_rate >= 1 or self.max_leverage <= 0
        ):
            raise ValueError("Invalid maintenance tier")


@dataclass(frozen=True)
class Maintenance:
    instrument: Instrument
    effective: int
    evidence: Evidence
    tiers: tuple[Tier, ...]

    def __post_init__(self) -> None:
        if not self.tiers or self.tiers[0].deduction:
            raise ValueError("Missing initial maintenance band")
        for lower, upper in zip(self.tiers, self.tiers[1:], strict=False):
            if (
                upper.ceiling <= lower.ceiling
                or upper.maintenance_rate < lower.maintenance_rate
                or upper.max_leverage > lower.max_leverage
                or upper.deduction != lower.deduction + lower.ceiling * (
                    upper.maintenance_rate - lower.maintenance_rate
                )
            ):
                raise ValueError("Discontinuous or unordered maintenance tiers")

    def tier(self, notional: Decimal) -> Tier | None:
        return next((tier for tier in self.tiers if notional <= tier.ceiling), None)


@dataclass(frozen=True)
class Spread:
    instrument: Instrument
    evidence: Evidence
    bps: Decimal

    def __post_init__(self) -> None:
        if not self.bps.is_finite() or not ZERO <= self.bps < 10000:
            raise ValueError("Invalid spread observation")


@dataclass(frozen=True)
class MarkGap:
    instrument: Instrument
    start: int
    end: int
    evidence: Evidence

    def __post_init__(self) -> None:
        if self.start >= self.end or self.evidence.provenance != Provenance.UNAVAILABLE:
            raise ValueError("A mark coverage gap must be bounded and disclosed")


@dataclass(frozen=True)
class Dataset:
    listings: tuple[Listing, ...]
    candles: tuple[Candle, ...]
    settlements: tuple[Funding, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    filters: tuple[Filters | FilterWithdrawal, ...] = ()
    maintenance: tuple[Maintenance, ...] = ()
    spreads: tuple[Spread, ...] = ()
    mark_gaps: tuple[MarkGap, ...] = ()


class Market:
    """Index observations by epoch. Lookup time always applies to as-of AND effective time."""

    def __init__(self, data: Dataset) -> None:
        self.listings = _index(data.listings, lambda x: (x.effective, x.evidence.as_of))
        self.bars = _index(data.candles, lambda x: (x.is_mark, x.end - x.start, x.start))
        self.settlements = _index(data.settlements, lambda x: (x.time,))
        self.schedules = _index(data.schedules, lambda x: (x.effective, x.evidence.as_of))
        self.filters = _index(data.filters, lambda x: (x.effective, x.evidence.as_of))
        self.maintenance = _index(data.maintenance, lambda x: (x.effective, x.evidence.as_of))
        self.spreads = _index(data.spreads, lambda x: (x.evidence.as_of,))
        self.mark_gaps = _index(data.mark_gaps, lambda x: (x.start, x.end))
        for index in (
            self.bars, self.settlements, self.schedules, self.filters,
            self.maintenance, self.spreads, self.mark_gaps,
        ):
            if index.keys() - self.listings.keys():
                raise ValueError("Observation references an unknown listing epoch")
        epochs: dict[str, list[Instrument]] = defaultdict(list)
        for instrument in self.listings:
            epochs[instrument.symbol].append(instrument)
            if any(x.effective < instrument.onboard for x in self.listings[instrument]):
                raise ValueError("Listing effective before its epoch")
        for group in epochs.values():
            for old, new in zip(sorted(group), sorted(group)[1:], strict=False):
                if old.onboard == new.onboard:
                    raise ValueError("Conflicting instrument identity")
                # An absence learned after relisting can withdraw stale metadata at
                # receipt, but cannot move the earlier, attested epoch boundary.
                end = max((
                    x.effective for x in self.listings[old]
                    if not x.tradable
                    and (x.status != "ABSENT_FROM_EXCHANGE_INFO" or x.effective <= new.onboard)
                ), default=-1)
                if not old.onboard <= end <= new.onboard:
                    raise ValueError("Relisting requires a distinct prior epoch boundary")
                if any(x.tradable and x.effective >= end for x in self.listings[old]):
                    raise ValueError("Trading observation overlaps a later listing epoch")
                if any(x.end > end for x in self.bars.get(old, ())):
                    raise ValueError("Candle crosses a listing epoch boundary")
                if any(x.time > end for x in self.settlements.get(old, ())):
                    raise ValueError("Settlement crosses a listing epoch boundary")
        self._bar_keys = {
            (bar.instrument, bar.is_mark, bar.end - bar.start, bar.start): bar
            for bars in self.bars.values() for bar in bars
        }

    def bar(self, instrument: Instrument, start: int, size: int, *, mark: bool = False) -> Candle | None:
        return self._bar_keys.get((instrument, mark, size, start))

    def history(self, instrument: Instrument, time: int, size: int, count: int) -> tuple[Candle, ...]:
        # Availability may place the latest eligible close behind the clock boundary.
        bars = self.bars.get(instrument, ())
        end = max((b.end for b in bars if not b.is_mark and b.end - b.start == size
                   and b.available <= time), default=0)
        if not end or time - end >= size:
            return ()
        result = []
        for start in range(end - count * size, end, size):
            bar = self.bar(instrument, start, size)
            if bar is None or bar.available > time or bar.evidence.provenance == Provenance.UNAVAILABLE:
                return ()
            result.append(bar)
        return tuple(result)


class _InstrumentObservation(Protocol):
    @property
    def instrument(self) -> Instrument: ...


class _EffectiveObservation(_InstrumentObservation, Protocol):
    @property
    def effective(self) -> int: ...

    @property
    def evidence(self) -> Evidence: ...


def _index[T: _InstrumentObservation](
    records: tuple[T, ...], key: Callable[[T], tuple[int, ...]]
) -> MappingProxyType[Instrument, tuple[T, ...]]:
    groups: dict[Instrument, dict[tuple[int, ...], T]] = defaultdict(dict)
    for record in records:
        group = groups[record.instrument]
        identity = key(record)
        if identity in group:
            raise ValueError(f"Duplicate observation for {record.instrument.id} at {identity}")
        group[identity] = record
    return MappingProxyType({
        instrument: tuple(group[k] for k in sorted(group))
        for instrument, group in groups.items()
    })


def as_of[T: _EffectiveObservation](records: tuple[T, ...], time: int) -> T | None:
    return next(
        (x for x in reversed(records) if x.effective <= time and x.evidence.as_of <= time), None
    )
