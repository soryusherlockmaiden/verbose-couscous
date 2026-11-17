"""Deterministic, isolated single-instrument replay; no exchange capability."""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from enum import StrEnum

from acgs.clock import Clock
from acgs.eligibility import (
    Candidate,
    EligibilityDecision,
    Side,
    current_spread,
    evaluate,
    execution_slippage,
    order_step,
    quantize_down,
    slipped,
    stop_price,
    survives,
)
from acgs.funding import funding_complete
from acgs.pit import (
    ZERO,
    Candle,
    Filters,
    Funding,
    Instrument,
    Maintenance,
    Market,
    Prices,
    Provenance,
    as_of,
)
from acgs.sim_config import Policy, StopBasis, digest, instant_ms
from acgs.sim_ledger import AccountSnapshot, Event, Journal, MarkSource


class ReplayError(ValueError):
    """Replay stopped without producing a successful result."""


class Action(StrEnum):
    ENTER = "ENTER"
    EXIT = "EXIT"
    TRAIL = "TRAIL"


@dataclass(frozen=True)
class Intent:
    time: int
    available: int
    action: Action
    side: Side
    distance: Decimal
    quantity: Decimal | None = None


@dataclass(frozen=True)
class ReplayPlan:
    candidate_version: str
    instrument: Instrument
    start: int
    interval: int
    equity: Decimal
    risk_pct: Decimal
    leverage: int
    intents: tuple[Intent, ...]
    halt_level: Decimal | None = None


@dataclass(frozen=True)
class Replay:
    run_digest: str
    dataset_digest: str
    policy_digest: str
    decisions: tuple[EligibilityDecision, ...]
    events: tuple[Event, ...]
    snapshots: tuple[AccountSnapshot, ...]


def replay(
    market: Market, dataset_digest: str, policy: Policy, clock: Clock, plan: ReplayPlan
) -> Replay:
    end = instant_ms(clock.now())
    with localcontext(policy.context()):
        return _Replay(market, dataset_digest, policy, plan, end).run()


class _Replay:
    def __init__(
        self, market: Market, dataset_digest: str, policy: Policy, plan: ReplayPlan, end: int,
    ) -> None:
        if (
            not plan.candidate_version or plan.interval <= 0 or end <= plan.start
            or plan.start % plan.interval or end % plan.interval
            or not plan.equity.is_finite() or plan.equity <= ZERO
        ):
            raise ReplayError("Invalid replay version, grid or starting equity")
        self.market = market
        self.dataset_digest = dataset_digest
        self.p = policy
        self.plan = plan
        self.end = end
        self.inst = plan.instrument
        if self.inst not in market.listings:
            raise ReplayError("Unknown listing epoch")
        self.run_id = digest((dataset_digest, policy.digest, asdict(plan), end))
        self.journal = Journal(plan.equity)
        self.liquidated = False
        self.decisions: list[EligibilityDecision] = []
        self.funding = market.settlements.get(self.inst, ())
        self.funding_keys = {funding.time: funding for funding in self.funding}
        self.intents: dict[tuple[int, Action], Intent] = {}
        for intent in plan.intents:
            key = intent.time, intent.action
            if (
                intent.time % plan.interval or intent.available > intent.time
                or not plan.start <= intent.time <= end or key in self.intents
                or (intent.action != Action.TRAIL and intent.time == end)
                or (intent.action == Action.TRAIL and intent.time == plan.start)
                or not intent.distance.is_finite() or intent.distance < ZERO
                or (intent.action != Action.EXIT and intent.distance == ZERO)
                or (intent.quantity is not None
                    and (not intent.quantity.is_finite() or intent.quantity <= ZERO))
            ):
                raise ReplayError("Invalid, late or duplicate replay intent")
            self.intents[key] = intent

    def run(self) -> Replay:
        for start in range(self.plan.start, self.end, self.plan.interval):
            self.bar(start)
        return Replay(
            self.run_id, self.dataset_digest, self.p.digest, tuple(self.decisions),
            tuple(self.journal.events), tuple(self.journal.snapshots),
        )

    def snapshot(
        self, seq: int, time: int, mark: Decimal | None, source: MarkSource, observable: bool = True,
    ) -> AccountSnapshot:
        return self.journal.snapshot(
            seq, time, mark, source, self.p.taker_fee + self.slippage(time, stress=True),
            observable, self.run_id, self.p.accounting_tolerance,
        )

    def inputs(self, time: int) -> tuple[Filters, Maintenance] | None:
        listing = as_of(self.market.listings[self.inst], time)
        filters = as_of(self.market.filters.get(self.inst, ()), time)
        tiers = as_of(self.market.maintenance.get(self.inst, ()), time)
        if listing is None or not listing.tradable or filters is None or tiers is None:
            return None
        for row in (filters, tiers):
            ev = row.evidence
            if (
                time - ev.as_of >= self.p.metadata_age_ms or ev.provenance == Provenance.UNAVAILABLE
                or (not self.p.allow_reconstructed and ev.provenance != Provenance.VERIFIED)
            ):
                return None
        return filters, tiers

    def marks(self, bar: Candle) -> tuple[Prices, MarkSource, MarkSource] | None:
        mark = self.market.bar(self.inst, bar.start, self.plan.interval, mark=True)
        if mark is not None and mark.evidence.provenance == Provenance.VERIFIED:
            return mark.prices, MarkSource.MARK_KLINE_OPEN, MarkSource.MARK_KLINE_CLOSE
        if self.p.allow_documented_mark_proxy and any(
            gap.start <= bar.start and bar.end <= gap.end and gap.evidence.as_of <= bar.start
            for gap in self.market.mark_gaps.get(self.inst, ())
        ):
            return bar.prices, MarkSource.CANDLE_OPEN_PROXY, MarkSource.CANDLE_CLOSE_PROXY
        return None

    def close_available(self, trade: Candle, source: MarkSource) -> bool:
        mark_bar = self.market.bar(self.inst, trade.start, self.plan.interval, mark=True)
        return trade.available <= trade.end and (
            source == MarkSource.CANDLE_CLOSE_PROXY
            or (mark_bar is not None and mark_bar.available <= trade.end)
        )

    def bar(self, start: int) -> None:
        end = start + self.plan.interval
        j = self.journal
        trade = self.market.bar(self.inst, start, self.plan.interval)
        due = tuple(f for f in self.funding if start <= f.time < end)
        entry = self.intents.get((start, Action.ENTER))
        exit_ = self.intents.get((start, Action.EXIT))
        if trade is None or trade.evidence.provenance == Provenance.UNAVAILABLE:
            if j.position.quantity or entry is not None or exit_ is not None:
                raise ReplayError(f"Contract candle unavailable at {start}")
            self.snapshot(j.record(end, "NO_DATA"), end, None, MarkSource.MARK_UNAVAILABLE, False)
            return
        marks = self.marks(trade)
        inputs = self.inputs(start)
        if marks is None or inputs is None:
            if j.position.quantity:
                raise ReplayError(f"Mark, listing or maintenance data unavailable for open position at {start}")
            if entry is not None and marks is not None:
                self.reject_inputs(entry, trade.prices.opening, marks[0].opening)
            elif entry is not None:
                j.record(start, "REJECTED", detail="MARK_UNAVAILABLE")
            seq = j.record(end, "BAR_CLOSE")
            if marks is None:
                self.snapshot(seq, end, None, MarkSource.MARK_UNAVAILABLE, False)
            else:
                prices, _, source = marks
                self.snapshot(seq, end, prices.close, source, self.close_available(trade, source))
            return
        mark, source_open, source_close = marks
        filters, tiers = inputs
        pos = j.position
        if pos.quantity and not funding_complete(self.funding, start, start + 1, self.p):
            raise ReplayError(f"Funding settlement continuity unavailable at {start}")
        at_open = self.funding_keys.get(start)
        if at_open is not None:
            self.post_funding(at_open, mark.opening, source_open)

        stop_path = mark if self.p.stop_basis == StopBasis.MARK_PRICE else trade.prices
        open_protection = self.protect(
            start, trade.prices.opening, mark.opening, stop_path.opening, mark.opening, source_open,
            filters, tiers, intrabar=False,
        )
        if exit_ is not None and not open_protection:
            self.exit_intent(exit_, trade.prices.opening, mark.opening, source_open, filters)
        elif exit_ is not None:
            j.record(start, "REJECTED", detail="PROTECTIVE_EXIT_AT_OPEN")
        if entry is not None:
            if self.liquidated:
                j.record(start, "REJECTED", detail="LIQUIDATION_LATCH")
            elif open_protection:
                j.record(start, "REJECTED", detail="PROTECTIVE_EXIT_AT_OPEN")
            else:
                self.enter(entry, trade.prices.opening, mark.opening, source_open, filters)

        if j.position.quantity:
            if self.listing_boundary(start, end):
                raise ReplayError(f"Position crosses an unresolved listing boundary at {end}")
            if tiers.tier(abs(j.position.quantity) * mark.high) is None:
                raise ReplayError(f"Mark path exceeds observed maintenance tier coverage at {start}")
            if not funding_complete(self.funding, start, end, self.p):
                raise ReplayError(f"Funding coverage unavailable during exposure at {start}")
            long = j.position.quantity > ZERO
            worst_mark = mark.low if long else mark.high
            worst_stop = stop_path.low if long else stop_path.high
            adverse_trade = trade.prices.low if long else trade.prices.high
            may_liquidate, may_stop = self.protection(worst_mark, worst_stop, tiers)
            inside = tuple(f for f in due if f.time > start)
            if inside and (may_liquidate or may_stop):
                raise ReplayError(f"Ambiguous intrabar protection/funding chronology at {start}")
            for f in inside:
                if f.mark is None:
                    raise ReplayError(f"Intrabar funding has no settlement mark at {f.time}")
                self.post_funding(f, f.mark, MarkSource.FUNDING_RECORD)
                if any(self.protection(worst_mark, worst_stop, tiers)):
                    raise ReplayError(f"Ambiguous intrabar funding-assisted protection at {f.time}")
            # OHLC supplies a bound, not an intrabar timestamp. Record the outcome
            # at interval end, before close/trailing and the next bar's open.
            self.protect(
                end, adverse_trade, worst_mark, worst_stop, mark.close, source_close,
                filters, tiers, intrabar=True, contract_stop=self.p.stop_basis == StopBasis.CONTRACT_PRICE,
                cost_time=start,
            )
        seq = j.record(end, "BAR_CLOSE")
        close_available = self.close_available(trade, source_close)
        self.snapshot(seq, end, mark.close, source_close, close_available)
        trail = self.intents.get((end, Action.TRAIL))
        if trail is not None and j.position.quantity:
            if close_available:
                self.trail(trail, stop_path.close, mark.close, filters)
            else:
                j.record(end, "REJECTED", detail="TRAIL_CLOSE_UNAVAILABLE")
        elif trail is not None:
            j.record(end, "REJECTED", detail="NO_POSITION")

    def listing_boundary(self, start: int, end: int) -> bool:
        """Exposure follows the exchange's effective state, even if evidence arrived later.

        Decisions stay causal (`as_of`). Accounting for a held position fails closed on
        a status change the exchange applied inside the bar, whenever it was recorded.
        """
        history = self.market.listings[self.inst]
        known = as_of(history, end)
        actual = next((x for x in reversed(history) if x.effective <= end), None)
        return (
            known is None or not known.tradable or actual is None or not actual.tradable
            or any(not x.tradable and start < x.effective <= end for x in history)
        )

    def post_funding(self, event: Funding, mark: Decimal, source: MarkSource) -> None:
        if self.journal.position.quantity:
            price = event.mark if event.mark is not None else mark
            seq = self.journal.settle(event.time, price, event.rate)
            self.snapshot(
                seq, event.time, price, MarkSource.FUNDING_RECORD if event.mark else source,
                event.evidence.as_of <= event.time,
            )
        else:
            self.journal.record(event.time, "FUNDING_FLAT")

    def reject_inputs(self, intent: Intent, price: Decimal, mark: Decimal) -> None:
        """Persist the taxonomy reasons for an entry refused on point-in-time inputs (§5.1)."""
        j = self.journal
        filters = as_of(self.market.filters.get(self.inst, ()), intent.time)
        execution = price if filters is None else slipped(
            price, intent.side, self.slippage(intent.time, stress=False), filters.tick,
        )
        decision = evaluate(self.market, Candidate(
            self.inst, intent.time, intent.side, execution, mark, intent.distance, j.wallet,
            j.collateral, self.plan.risk_pct, self.plan.leverage, self.plan.halt_level,
            max_quantity=intent.quantity,
        ), self.p)
        if decision.executable:
            decision = replace(decision, reasons=("UNKNOWN_CONSTRAINT",))
        self.decisions.append(decision)
        j.record(intent.time, "REJECTED", detail="PIT_INPUTS")

    def enter(
        self, intent: Intent, price: Decimal, mark: Decimal, source: MarkSource, filters: Filters,
    ) -> None:
        j = self.journal
        pos = j.position
        if pos.quantity * intent.side < ZERO:
            j.record(intent.time, "REJECTED", detail="POSITION_SIDE")
            return
        rate = self.slippage(intent.time, stress=False)
        execution = slipped(price, intent.side, rate, filters.tick)
        closing_rate = self.p.taker_fee + self.slippage(intent.time, stress=True)
        cycle_equity = j.wallet + pos.quantity * (mark - pos.basis) - abs(pos.quantity) * mark * closing_rate
        open_risk = max(ZERO, pos.quantity * (mark - pos.stop))
        position_cap = min(self.p.exposure_cap / execution, filters.market_lot.maximum, filters.lot.maximum)
        remaining_exposure = max(ZERO, position_cap - abs(pos.quantity))
        max_qty = min(intent.quantity, remaining_exposure) if intent.quantity else remaining_exposure
        candidate = Candidate(
            self.inst, intent.time, intent.side, execution, mark, intent.distance, cycle_equity,
            j.collateral, self.plan.risk_pct, self.plan.leverage, self.plan.halt_level,
            open_risk, max_qty,
        )
        decision = evaluate(self.market, candidate, self.p)
        self.decisions.append(decision)
        j.record(intent.time, "SUBMITTED")
        if not decision.executable:
            j.record(intent.time, "REJECTED", detail=",".join(decision.reasons))
            return
        ordered = decision.quantity
        qty = quantize_down(ordered * self.p.fill_fraction, order_step(filters))
        if qty < ordered:
            # The revalidated fraction is the decision that authorizes the fill.
            partial = evaluate(self.market, replace(candidate, max_quantity=qty), self.p)
            self.decisions.append(partial)
            if not partial.executable:
                j.record(intent.time, "CANCELED", detail="PARTIAL_FILL_NOT_EXECUTABLE")
                return
            decision = partial
            qty = partial.quantity
        stop = decision.stop
        if pos.quantity:
            stop = max(stop, pos.stop) if intent.side == Side.LONG else min(stop, pos.stop)
        trigger_open = mark if self.p.stop_basis == StopBasis.MARK_PRICE else price
        if intent.side * (trigger_open - stop) <= ZERO:
            j.record(intent.time, "REJECTED", detail="STOP_IMMEDIATELY_TRIGGERABLE")
            return
        if pos.quantity:
            total = abs(pos.quantity) + qty
            basis = (abs(pos.quantity) * pos.basis + qty * execution) / total
            boundary = slipped(stop, Side(-intent.side), self.slippage(intent.time, stress=True),
                               filters.tick) - intent.side * decision.liquidation_buffer
            inputs = self.inputs(intent.time)
            if inputs is None:
                raise ReplayError("Risk metadata changed during modeled fill")
            tiers = inputs[1]
            tier = tiers.tier(total * max(mark, execution))
            if tier is None or self.plan.leverage > tier.max_leverage or any(
                not survives(tiers, total * intent.side, basis, pos.margin + decision.margin,
                             checked, self.p.liquidation_fee)
                for checked in (mark, boundary)
            ):
                j.record(intent.time, "REJECTED", detail="COMBINED_LIQUIDATION_BUFFER")
                return
        kind = "PARTIAL_FILL" if qty < ordered else "ENTRY_FILL"
        seq = j.fill(
            intent.time, kind, qty * intent.side, execution, self.p.taker_fee,
            margin=decision.margin, reserve=decision.reserve, stop=stop,
        )
        self.snapshot(seq, intent.time, mark, source)
        if kind == "PARTIAL_FILL":
            j.record(intent.time, "CANCELED", detail="UNFILLED_REMAINDER")

    def exit_intent(
        self, intent: Intent, price: Decimal, mark: Decimal, source: MarkSource, filters: Filters,
    ) -> None:
        j = self.journal
        pos = j.position
        if not pos.quantity:
            j.record(intent.time, "REJECTED", detail="NO_POSITION")
            return
        side = Side.SHORT if pos.quantity > ZERO else Side.LONG
        if intent.side != side:
            j.record(intent.time, "REJECTED", detail="EXIT_DIRECTION")
            return
        qty = abs(pos.quantity)
        if intent.quantity is not None:
            qty = min(qty, quantize_down(intent.quantity, order_step(filters)))
        if not qty or (qty < abs(pos.quantity) and qty < filters.market_lot.minimum):
            j.record(intent.time, "REJECTED", detail="MIN_QTY")
            return
        fill = slipped(price, side, self.slippage(intent.time, stress=False), filters.tick)
        seq = j.fill(intent.time, "EXIT_FILL", qty * side, fill, self.p.taker_fee)
        self.snapshot(seq, intent.time, mark, source)

    def protection(self, mark: Decimal, trigger: Decimal, tiers: Maintenance) -> tuple[bool, bool]:
        pos = self.journal.position
        if not pos.quantity:
            return False, False
        stop = trigger <= pos.stop if pos.quantity > ZERO else trigger >= pos.stop
        liquidate = not survives(tiers, pos.quantity, pos.basis, pos.margin, mark, self.p.liquidation_fee)
        return liquidate, stop

    def protect(
        self, time: int, trade: Decimal, mark: Decimal, trigger: Decimal, snapshot_mark: Decimal,
        source: MarkSource, filters: Filters, tiers: Maintenance, *, intrabar: bool,
        contract_stop: bool = False, cost_time: int | None = None,
    ) -> bool:
        liquidate, stop = self.protection(mark, trigger, tiers)
        if not liquidate and not stop:
            return False
        pos = self.journal.position
        exit_side = Side.SHORT if pos.quantity > ZERO else Side.LONG
        basis = pos.stop if intrabar and contract_stop and not liquidate else trade
        rate = self.slippage(cost_time if cost_time is not None else time, stress=True)
        price = max(filters.tick, slipped(basis, exit_side, rate, filters.tick))
        seq = self.journal.fill(
            time, "LIQUIDATION" if liquidate else "STOP", -pos.quantity, price,
            self.p.taker_fee + (self.p.liquidation_fee if liquidate else ZERO),
            detail="LIQUIDATION_CANNOT_BE_RULED_OUT_BEFORE_STOP" if liquidate and stop else
                   "INTRABAR_OHLC_BOUND" if intrabar else "OPEN_GAP",
            forfeit=pos.margin + self.journal.credits if liquidate else None,
        )
        self.liquidated |= liquidate
        self.snapshot(seq, time, snapshot_mark, source, not intrabar)
        return True

    def trail(self, intent: Intent, closing_price: Decimal, mark: Decimal, filters: Filters) -> None:
        pos = self.journal.position
        side = Side.LONG if pos.quantity > ZERO else Side.SHORT
        candidate = stop_price(closing_price, intent.distance, side, filters.tick)
        if intent.side != side:
            reason = "POSITION_SIDE"
        elif not (
            filters.min_price <= candidate <= filters.max_price and candidate > ZERO
            and mark * filters.percent_down <= candidate <= mark * filters.percent_up
        ):
            reason = "PRICE_TICK"
        elif side * (closing_price - candidate) <= ZERO:
            reason = "STOP_IMMEDIATELY_TRIGGERABLE"
        elif side * (candidate - pos.stop) <= ZERO:
            reason = "TRAIL_NOT_FAVORABLE"
        else:
            self.journal.position = replace(pos, stop=candidate)
            self.journal.record(intent.time, "TRAIL_RATCHET", price=candidate)
            return
        # Keep the existing stop and record why the step-wise update was not applied.
        self.journal.record(intent.time, "REJECTED", price=candidate, detail=reason)

    def slippage(self, time: int, *, stress: bool) -> Decimal:
        rate = self.p.stressed_slippage if stress else self.p.slippage
        return execution_slippage(current_spread(self.market, self.inst, time, self.p), rate)
