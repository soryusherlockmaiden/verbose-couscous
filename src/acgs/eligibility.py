"""Pure stop-risk and isolated-collateral authorization for offline candidates."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import IntEnum
from math import gcd, lcm

from acgs.funding import funding_view
from acgs.pit import (
    ZERO,
    Evidence,
    Filters,
    Instrument,
    Maintenance,
    Market,
    Provenance,
    Spread,
    as_of,
)
from acgs.sim_config import Policy


class Side(IntEnum):
    LONG = 1
    SHORT = -1


@dataclass(frozen=True)
class Candidate:
    instrument: Instrument
    time: int
    side: Side
    entry: Decimal
    mark: Decimal
    stop_distance: Decimal
    cycle_equity: Decimal
    collateral: Decimal
    risk_pct: Decimal
    leverage: int
    halt_level: Decimal | None = None
    open_risk: Decimal = ZERO
    max_quantity: Decimal | None = None
    opening_excess: Decimal = ZERO


@dataclass(frozen=True)
class EligibilityDecision:
    candidate: Candidate
    policy_digest: str
    filter_evidence: Evidence | None
    tier_evidence: Evidence | None
    funding_evidence: Evidence
    spread_evidence: Evidence | None
    reasons: tuple[str, ...]
    binding: str | None = None
    risk_budget: Decimal = ZERO
    requested: Decimal = ZERO
    quantity: Decimal = ZERO
    notional: Decimal = ZERO
    stop: Decimal = ZERO
    margin: Decimal = ZERO
    maintenance: Decimal = ZERO
    reserve: Decimal = ZERO
    stressed_loss: Decimal = ZERO
    liquidation_buffer: Decimal = ZERO
    risk_utilization: Decimal = ZERO
    caps: tuple[tuple[str, Decimal], ...] = ()

    @property
    def executable(self) -> bool:
        return not self.reasons

    @property
    def provenance(self) -> Provenance:
        items = (self.filter_evidence, self.tier_evidence, self.funding_evidence, self.spread_evidence)
        if any(e is None or e.provenance == Provenance.UNAVAILABLE for e in items):
            return Provenance.UNAVAILABLE
        # Fee, fill and liquidation assumptions remain simulated even with verified inputs.
        if any(e is not None and e.provenance == Provenance.RECONSTRUCTED for e in items):
            return Provenance.RECONSTRUCTED
        return Provenance.VERIFIED


def quantize_down(value: Decimal, step: Decimal) -> Decimal:
    return (value // step) * step


def quantize_up(value: Decimal, step: Decimal) -> Decimal:
    lower = quantize_down(value, step)
    return lower if lower == value else lower + step


def order_step(filters: Filters) -> Decimal:
    a, b = filters.lot.step.as_integer_ratio()
    c, d = filters.market_lot.step.as_integer_ratio()
    return Decimal(lcm(a, c)) / gcd(b, d)


def stop_price(entry: Decimal, distance: Decimal, side: Side, tick: Decimal) -> Decimal:
    target = entry - side * distance
    if target <= ZERO:
        return ZERO
    return quantize_up(target, tick) if side == Side.LONG else quantize_down(target, tick)


def slipped(price: Decimal, direction: Side, rate: Decimal, tick: Decimal) -> Decimal:
    adverse = price * (1 + direction * rate)
    return quantize_up(adverse, tick) if direction == Side.LONG else quantize_down(adverse, tick)


def current_spread(market: Market, instrument: Instrument, time: int, policy: Policy) -> Spread | None:
    return next(
        (x for x in reversed(market.spreads.get(instrument, ()))
         if 0 <= time - x.evidence.as_of < policy.metadata_age_ms), None,
    )


def execution_slippage(spread: Spread | None, modeled_rate: Decimal) -> Decimal:
    if spread is None or spread.evidence.provenance == Provenance.UNAVAILABLE:
        return modeled_rate
    return max(modeled_rate, spread.bps / 20_000)


def _liquidation_cap(
    maintenance: Maintenance, entry: Decimal, boundary: Decimal, mark: Decimal,
    side: Side, leverage: int, liquidation_fee: Decimal,
) -> Decimal:
    # For isolated margin q*entry/L, survival at price p requires
    # q*(entry/L + side*(p-entry) - p*(MMR + liquidation_fee)) + deduction > 0.
    # Margin-health is continuous across tiers. Solve the inequality per tier at
    # both the stressed stop boundary and the current mark.
    cap = maintenance.tiers[-1].ceiling / entry
    for price in (entry, boundary, mark):
        if price <= ZERO:
            return ZERO
        lower = ZERO
        for tier in maintenance.tiers:
            if tier.max_leverage < leverage:
                cap = min(cap, lower / price)
                break
            per_unit = (
                entry / leverage + side * (price - entry)
                - price * (tier.maintenance_rate + liquidation_fee)
            )
            if per_unit < ZERO:
                threshold = tier.deduction / -per_unit
                if threshold <= tier.ceiling / price:
                    cap = min(cap, threshold)
                    break
            lower = tier.ceiling
        cap = min(cap, maintenance.tiers[-1].ceiling / price)
    return cap


def evaluate(market: Market, order: Candidate, policy: Policy) -> EligibilityDecision:
    with localcontext(policy.context()):
        return _evaluate(market, order, policy)


def _evaluate(market: Market, o: Candidate, p: Policy) -> EligibilityDecision:
    listing = as_of(market.listings.get(o.instrument, ()), o.time)
    filters = as_of(market.filters.get(o.instrument, ()), o.time)
    maintenance = as_of(market.maintenance.get(o.instrument, ()), o.time)
    view = funding_view(market, o.instrument, o.time, p)
    spread = current_spread(market, o.instrument, o.time, p)
    spread_evidence = spread.evidence if spread is not None else None
    # A declared adverse slippage scenario can cover missing historical order books.
    if spread_evidence is None and p.allow_reconstructed and p.stressed_slippage > ZERO:
        spread_evidence = Evidence(
            "scenario:" + p.digest, "stressed_slippage", Provenance.RECONSTRUCTED, o.time,
            "No observed spread; cost stress is a scenario",
        )
    evidence = (
        filters.evidence if filters else None, maintenance.evidence if maintenance else None,
        view.evidence, spread_evidence,
    )
    reasons: list[str] = []
    if listing is None or not listing.tradable or filters is None:
        reasons.append("MISSING_PIT_FILTER")
    critical = (*evidence[:2], listing.evidence if listing else None)
    if any(e is not None and (
        e.provenance == Provenance.UNAVAILABLE
        or (e.provenance == Provenance.RECONSTRUCTED and not p.allow_reconstructed)
    ) for e in critical):
        reasons.append("UNVERIFIED_CRITICAL_FILTER")
    if any(e is not None and o.time - e.as_of >= p.metadata_age_ms for e in critical[:2]):
        reasons.append("STALE_METADATA")
    if (
        view.interval_ms is None or view.evidence.provenance == Provenance.UNAVAILABLE
        or (view.evidence.provenance == Provenance.RECONSTRUCTED and not p.allow_reconstructed)
    ):
        reasons.append("FEE_FUNDING_RESERVE")
    if spread_evidence is None or spread_evidence.provenance == Provenance.UNAVAILABLE or (
        spread_evidence.provenance == Provenance.RECONSTRUCTED and not p.allow_reconstructed
    ):
        reasons.append("SPREAD_COST_UNCERTAIN")
    if maintenance is None:
        reasons.append("LIQUIDATION_BUFFER")
    values = (o.entry, o.mark, o.stop_distance, o.cycle_equity, o.collateral, o.risk_pct,
              o.open_risk, o.opening_excess)
    if any(not x.is_finite() for x in values) or not (
        min(o.entry, o.mark, o.stop_distance) > ZERO and ZERO < o.risk_pct <= 1
        and o.leverage >= 1 and o.open_risk >= ZERO and o.opening_excess >= ZERO
        and (o.max_quantity is None or o.max_quantity.is_finite() and o.max_quantity >= ZERO)
        and (o.halt_level is None or o.halt_level.is_finite() and o.halt_level >= ZERO)
    ):
        reasons.append("UNKNOWN_CONSTRAINT")
    if reasons or filters is None or maintenance is None or view.interval_ms is None:
        return EligibilityDecision(o, p.digest, *evidence, tuple(reasons))

    budget = o.cycle_equity * o.risk_pct
    uncapped_budget = budget
    if o.halt_level is not None:
        budget = min(budget, o.cycle_equity - o.halt_level - p.halt_buffer - o.open_risk)
    if budget <= ZERO:
        reason = "HALT_HEADROOM" if budget < uncapped_budget else "RISK_BUDGET"
        return EligibilityDecision(o, p.digest, *evidence, (reason,), risk_budget=budget)
    exit_side = Side(-o.side)
    stop = stop_price(o.entry, o.stop_distance, o.side, filters.tick)
    slip = execution_slippage(spread, p.stressed_slippage)
    stressed_exit = slipped(stop, exit_side, slip, filters.tick) if stop else ZERO
    fee_per_unit = p.taker_fee * (o.entry + max(o.entry, stressed_exit))
    # Both directions can pay funding. A published cap/floor can require more reserve.
    publication = view.publication
    funding_rate = p.funding_reserve_rate
    if publication is not None:
        funding_rate = max(funding_rate, abs(publication.cap or ZERO), abs(publication.floor or ZERO))
    payments = (p.funding_reserve_horizon_ms + view.interval_ms - 1) // view.interval_ms + 1
    funding_rate *= payments
    reserve_per_unit = fee_per_unit + max(o.entry, o.mark, stressed_exit) * funding_rate
    cost = fee_per_unit + o.entry * slip + abs(stop - stressed_exit) + filters.tick
    risk_cap = budget / (o.stop_distance + cost)
    collateral = max(ZERO, o.collateral - o.opening_excess)
    margin_rate = o.entry / (p.margin_utilization * o.leverage)
    margin_cap = max(ZERO, collateral - p.fixed_reserve) / (margin_rate + reserve_per_unit)
    buffer = o.entry * p.liquidation_buffer_rate
    boundary = stressed_exit - o.side * buffer
    liq_cap = _liquidation_cap(
        maintenance, o.entry, boundary, o.mark, o.side, o.leverage, p.liquidation_fee
    ) if stop else ZERO
    limit_cap = min(filters.lot.maximum, filters.market_lot.maximum, p.exposure_cap / o.entry)
    if o.max_quantity is not None:
        limit_cap = min(limit_cap, o.max_quantity)
    caps = (("RISK", risk_cap), ("MARGIN", margin_cap), ("LIQUIDATION", liq_cap), ("LIMIT", limit_cap))
    binding, requested = min(caps, key=lambda item: item[1])
    step = order_step(filters)
    qty = quantize_down(requested, step)
    # A strict maintenance boundary can land precisely on a quantity step, at the
    # stressed stop or at the current mark. Step down once instead of rejecting.
    if qty and qty == liq_cap and not all(
        survives(maintenance, qty * o.side, o.entry, qty * o.entry / o.leverage, price, p.liquidation_fee)
        for price in (boundary, o.mark)
    ):
        qty -= step
    notional = qty * o.entry
    min_qty = max(filters.lot.minimum, filters.market_lot.minimum)
    minimum = quantize_up(max(min_qty, filters.min_notional / min(o.entry, o.mark)), step)
    if qty < min_qty:
        reasons.append("MIN_QTY")
    if min(notional, qty * o.mark) < filters.min_notional:
        reasons.append("MIN_NOTIONAL")
    if not qty:
        reasons.append("STEP_SIZE")
    if qty < minimum:
        if minimum > risk_cap:
            reasons.append("HALT_HEADROOM" if budget < uncapped_budget else "RISK_BUDGET")
        if minimum > margin_cap:
            reasons.append(
                "INSUFFICIENT_COLLATERAL" if minimum * margin_rate > collateral
                else "FEE_FUNDING_RESERVE"
            )
        if minimum > liq_cap:
            reasons.append("LIQUIDATION_BUFFER")
    # PRICE_FILTER bounds price and stopPrice. PERCENT_PRICE bounds a limit `price` only,
    # so it never rejects the protective stop trigger (Binance USD-M symbol filters).
    if (
        stop <= ZERO or stressed_exit <= ZERO or stop == o.entry or o.entry % filters.tick
        or not filters.min_price <= stop <= filters.max_price
        or not filters.min_price <= o.entry <= filters.max_price
        or not o.mark * filters.percent_down <= o.entry <= o.mark * filters.percent_up
    ):
        reasons.append("PRICE_TICK")
    loss = qty * (abs(o.entry - stop) + cost)
    if loss > budget and "RISK_BUDGET" not in reasons:
        reasons.append("RISK_BUDGET")
    tier = maintenance.tier(qty * o.mark)
    if qty and (
        tier is None or o.leverage > tier.max_leverage
        or not survives(maintenance, qty * o.side, o.entry, notional / o.leverage,
                        o.mark, p.liquidation_fee)
        or not survives(maintenance, qty * o.side, o.entry, notional / o.leverage,
                        boundary, p.liquidation_fee)
    ) and "LIQUIDATION_BUFFER" not in reasons:
        reasons.append("LIQUIDATION_BUFFER")
    return EligibilityDecision(
        o, p.digest, *evidence, tuple(reasons), binding, budget, requested, qty,
        notional, stop, notional / o.leverage,
        ZERO if tier is None else qty * o.mark * tier.maintenance_rate - tier.deduction,
        p.fixed_reserve + qty * reserve_per_unit, loss, buffer, loss / budget, caps,
    )


def survives(
    maintenance: Maintenance, signed_qty: Decimal, basis: Decimal, margin: Decimal,
    mark: Decimal, liquidation_fee: Decimal,
) -> bool:
    notional = abs(signed_qty) * mark
    tier = maintenance.tier(notional)
    return tier is not None and mark > ZERO and (
        margin + signed_qty * (mark - basis)
        > notional * (tier.maintenance_rate + liquidation_fee) - tier.deduction
    )
