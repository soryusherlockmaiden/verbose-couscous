"""Technical execution-feasibility grid. No strategy selection or profit estimates."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any

from acgs.clock import Clock, FixedClock
from acgs.eligibility import (
    Candidate,
    EligibilityDecision,
    Side,
    current_spread,
    evaluate,
    execution_slippage,
    slipped,
)
from acgs.pit import DAY, ZERO, Candle, Market, Provenance, as_of
from acgs.sim_config import EPOCH, Policy, instant_ms
from acgs.universe import snapshot


@dataclass(frozen=True)
class Scenario:
    phase: str
    equity: Decimal
    risk: Decimal
    leverage: int
    stop_factor: Decimal
    cost_factor: Decimal

    @property
    def id(self) -> str:
        return f"{self.phase}/{self.equity}/{self.risk}/{self.stop_factor}/{self.cost_factor}"


def _atr(bars: tuple[Candle, ...]) -> Decimal:
    ranges = [
        max(bar.prices.high - bar.prices.low, abs(bar.prices.high - prev.prices.close),
            abs(bar.prices.low - prev.prices.close))
        for prev, bar in zip(bars, bars[1:], strict=False)
    ]
    # Feasibility probe on the daily liquidity grid; SC-04 owns strategy ATR semantics.
    atr = sum(ranges[:14], ZERO) / 14
    for true_range in ranges[14:]:
        atr = (atr * 13 + true_range) / 14
    return atr


def _distribution(values: list[Decimal]) -> dict[str, Decimal] | None:
    if not values:
        return None
    values.sort()
    n = len(values)
    return {"min": values[0], "p50": (values[(n - 1) // 2] + values[n // 2]) / 2, "max": values[-1]}


# Reasons that leave a critical input present but unusable at the decision time.
UNUSABLE_INPUT = frozenset({
    "STALE_METADATA", "UNVERIFIED_CRITICAL_FILTER", "SPREAD_COST_UNCERTAIN",
})


@dataclass(frozen=True)
class _Attempt:
    decision: EligibilityDecision
    provenance: Provenance
    # Provenance labels the inputs; assessability says whether they could decide.
    assessable: bool


def _summary(attempts: list[_Attempt]) -> dict[str, Any]:
    rows = [(a.decision, a.provenance) for a in attempts]
    accepted = [d for d, _ in rows if d.executable]
    symbols: dict[str, list[Decimal]] = defaultdict(list)
    for d in accepted:
        symbols[d.candidate.instrument.id].append(d.risk_utilization)
    return {
        "attempted": len(rows),
        "assessable": sum(a.assessable for a in attempts),
        "executable": len(accepted),
        "executable_rate": Decimal(len(accepted)) / len(rows) if rows else None,
        "provenance": dict(Counter(p for _, p in rows)),
        "executable_provenance": dict(Counter(p for d, p in rows if d.executable)),
        "binding": dict(Counter(d.binding for d, _ in rows if d.binding is not None)),
        "executable_binding": dict(Counter(d.binding for d in accepted if d.binding is not None)),
        "rejection_reasons": dict(Counter(reason for d, _ in rows for reason in d.reasons)),
        "quantity": _distribution([d.quantity for d in accepted]),
        "notional": _distribution([d.notional for d in accepted]),
        "margin": _distribution([d.margin for d in accepted]),
        "risk_utilization_by_symbol": {i: _distribution(v) for i, v in symbols.items()},
    }


def _status(attempts: list[_Attempt]) -> str:
    """No executable order is a no-trade finding only if some attempt was assessable.

    When every attempt lacks a usable critical input, the window cannot show
    infeasibility (PRD §6.1, SPEC §4.3): the result is inconclusive, not no-trade.
    """
    if not attempts:
        return "INCONCLUSIVE_NO_ASSESSABLE_ATTEMPTS"
    if any(a.decision.executable for a in attempts):
        return "ASSESSABLE"
    if not any(a.assessable for a in attempts):
        return "INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE"
    return "NO_TRADE_IN_DECLARED_WINDOW"


def feasibility(
    market: Market, dataset_digest: str, policy: Policy, start: int, clock: Clock,
) -> dict[str, Any]:
    """Daily UTC boundary + 60 sec probe, through the injected exclusive end."""
    end = instant_ms(clock.now())
    if end <= start or start % DAY or end % DAY:
        raise ValueError("Feasibility window must align to UTC days")
    with localcontext(policy.context()):
        return _feasibility(market, dataset_digest, policy, start, end)


def _feasibility(
    market: Market, dataset_digest: str, policy: Policy, start: int, end: int,
) -> dict[str, Any]:
    phases = (
        ("IGNITION", (Decimal(50),), policy.ign_risks, policy.ignition_leverage),
        ("GROWTH", policy.growth_equities, policy.growth_risks, policy.growth_leverage),
    )
    cases = tuple(
        Scenario(phase, equity, risk, leverage, stop, cost)
        for phase, equities, risks, leverage in phases
        for equity in equities for risk in risks
        for stop in policy.stop_factors for cost in policy.cost_factors
    )
    policy_variants = {
        scale: replace(
            policy, slippage=policy.slippage * scale,
            stressed_slippage=policy.stressed_slippage * scale,
            taker_fee=policy.taker_fee * scale,
            funding_reserve_rate=policy.funding_reserve_rate * scale,
        ) for scale in policy.cost_factors
    }
    snapshots = []
    decisions = []
    results: dict[str, list[_Attempt]] = defaultdict(list)
    exclusions: Counter[str] = Counter()
    slices: dict[tuple[str, str, str], list[_Attempt]] = defaultdict(list)
    for day in range(start, end, DAY):
        time = day + 60_000
        universe = snapshot(market, FixedClock(EPOCH + timedelta(milliseconds=time)), policy)
        snapshots.append(asdict(universe))
        for rank in universe.ranks:
            if rank.reason != "CANDIDATE":
                exclusions[rank.reason] += 1
                continue
            inst = rank.instrument
            bars = market.history(inst, time, DAY, policy.history_days)
            last = bars[-1]
            mark = market.bar(inst, last.start, DAY, mark=True)
            proxy = False
            if mark is None or mark.available > time or mark.evidence.provenance != Provenance.VERIFIED:
                proxy = policy.allow_documented_mark_proxy and any(
                    gap.start <= last.start and last.end <= gap.end and gap.evidence.as_of <= time
                    for gap in market.mark_gaps.get(inst, ())
                )
                if not proxy:
                    exclusions["MARK_UNAVAILABLE"] += 1
                    continue
                mark = last
            if rank.momentum is None:
                raise ValueError("Candidate missing signed momentum")
            side = Side.LONG if rank.momentum > ZERO else Side.SHORT
            atr = _atr(bars)
            regime = "POSITIVE_14D" if side == Side.LONG else "NEGATIVE_14D"
            filters = as_of(market.filters.get(inst, ()), time)
            spread = current_spread(market, inst, time, policy)
            for case in cases:
                config = policy_variants[case.cost_factor]
                price = last.prices.close
                if filters is not None:
                    price = slipped(price, side, execution_slippage(spread, config.slippage), filters.tick)
                decision = evaluate(market, Candidate(
                    inst, time, side, price, mark.prices.close, case.stop_factor * policy.atr_multiple * atr,
                    case.equity, case.equity, case.risk, case.leverage,
                    Decimal(30) if case.phase == "IGNITION" else None,
                ), config)
                provenance = decision.provenance
                if provenance == Provenance.VERIFIED and (
                    proxy or any(e.provenance != Provenance.VERIFIED for e in rank.evidence)
                ):
                    provenance = Provenance.RECONSTRUCTED
                attempt = _Attempt(decision, provenance, (
                    provenance != Provenance.UNAVAILABLE
                    and (config.allow_reconstructed or provenance != Provenance.RECONSTRUCTED)
                    and not UNUSABLE_INPUT.intersection(decision.reasons)
                ))
                results[case.id].append(attempt)
                slices[case.id, inst.id, regime].append(attempt)
                decisions.append({
                    "scenario": case.id, "regime": regime, "provenance": provenance,
                    "assessable": attempt.assessable,
                    "mark_source": "CANDLE_CLOSE_PROXY" if proxy else "MARK_KLINE_CLOSE",
                    "atr": atr, "executable": decision.executable, **asdict(decision),
                })
    return {
        "version": "m1-feasibility-v5", "dataset_digest": dataset_digest,
        "policy_digest": policy.digest, "start": start, "end_exclusive": end,
        "status": _status([row for items in results.values() for row in items]),
        "universe_exclusions": dict(exclusions), "universe_snapshots": snapshots,
        "scenarios": {case.id: {**asdict(case), **_summary(results[case.id])} for case in cases},
        "symbol_regime": {"/".join(key): _summary(rows) for key, rows in sorted(slices.items())},
        "decisions": decisions,
    }
