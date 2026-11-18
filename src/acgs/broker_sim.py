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

REPLAY_VERSION = "m1-replay-v5"


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
    version: str
    run_digest: str
    dataset_digest: str
    policy_digest: str
    decisions: tuple[EligibilityDecision, ...]
    events: tuple[Event, ...]
    snapshots: tuple[AccountSnapshot, ...]


@dataclass(frozen=True)
class DeferredTrail:
    intent: Intent
    closing_price: Decimal
    position_epoch: int


@dataclass(frozen=True)
class _Exposure:
    """A modeled fill: the instants it could have executed and the position around it."""

    earliest: int
    latest: int
    before: Decimal
    after: Decimal


@dataclass
class _Ownership:
    """A settlement whose holder can still change before its bar's OHLC is known.

    `posted` never exceeds min(worst known cash, 0): a credit stays withheld until the
    bar resolves, so no published snapshot depends on prices after the settlement.
    """

    event: Funding
    price: Decimal
    posted: Decimal
    final: Decimal
    holder: Decimal


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
        self.run_id = digest((REPLAY_VERSION, dataset_digest, policy.digest, asdict(plan), end))
        self.journal = Journal(plan.equity)
        self.cash_available_at = plan.start
        self.position_epoch = 0
        self.deferred_trails: dict[int, list[DeferredTrail]] = {}
        self.liquidated = False
        self.decisions: list[EligibilityDecision] = []
        self.funding = market.settlements.get(self.inst, ())
        # Settlements within the ownership window of a bar boundary settle at that boundary.
        # The holder is ambiguous across any fill in the window; the adverse candidate posts.
        self.window = policy.funding_ownership_window_ms
        if 2 * self.window >= plan.interval:
            raise ReplayError("Funding ownership window must be shorter than half a bar")
        self.boundary_funding: dict[int, Funding] = {}
        self.inside_funding: dict[int, list[Funding]] = {}
        for event in self.funding:
            nearest = (event.time + plan.interval // 2) // plan.interval * plan.interval
            if abs(event.time - nearest) <= self.window:
                if nearest in self.boundary_funding:
                    raise ReplayError(f"Two settlements share the bar boundary {nearest}")
                self.boundary_funding[nearest] = event
            else:
                bar_start = event.time // plan.interval * plan.interval
                self.inside_funding.setdefault(bar_start, []).append(event)
        self.exposures: list[_Exposure] = []
        self.ownership: list[_Ownership] = []
        self.last_close: tuple[int, Decimal, MarkSource] | None = None
        self.intents: dict[tuple[int, Action], Intent] = {}
        for intent in plan.intents:
            key = intent.time, intent.action
            if (
                intent.time % plan.interval
                or (intent.action != Action.TRAIL and intent.available > intent.time)
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
        horizon = self.boundary_funding.get(self.end)
        if horizon is not None and horizon.time < self.end:
            # A settlement just before the horizon still belongs to this replay. Its
            # holder window outlives the replay, so a credit stays withheld: a longer
            # horizon must publish the same values at this timestamp.
            self.boundary_settlement(self.end, None, record_flat=False, resolvable=True)
        self.ownership.clear()
        try:
            self.journal.verify(self.p.accounting_tolerance)
        except ValueError as error:
            raise ReplayError(str(error)) from error
        return Replay(
            REPLAY_VERSION, self.run_id, self.dataset_digest, self.p.digest, tuple(self.decisions),
            tuple(self.journal.events), tuple(self.journal.snapshots),
        )

    def snapshot(
        self, seq: int, time: int, mark: Decimal | None, source: MarkSource,
        available_at: int | None,
    ) -> AccountSnapshot:
        return self.journal.snapshot(
            seq, time, mark, source, self.p.taker_fee + self.slippage(time, stress=True),
            max(available_at, self.cash_available_at) if available_at is not None else None,
            self.run_id, self.p.accounting_tolerance,
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

    def close_available_at(self, trade: Candle, source: MarkSource) -> int:
        if source == MarkSource.CANDLE_CLOSE_PROXY:
            return trade.available
        mark_bar = self.market.bar(self.inst, trade.start, self.plan.interval, mark=True)
        if mark_bar is None:
            raise ReplayError("Mark close has no availability record")
        return max(trade.available, mark_bar.available)

    def bar(self, start: int) -> None:
        end = start + self.plan.interval
        j = self.journal
        trade = self.market.bar(self.inst, start, self.plan.interval)
        entry = self.intents.get((start, Action.ENTER))
        exit_ = self.intents.get((start, Action.EXIT))
        trail = self.intents.get((end, Action.TRAIL))
        deferred = self.deferred_trails.pop(start, [])
        if trade is None or trade.evidence.provenance == Provenance.UNAVAILABLE:
            if j.position.quantity or entry is not None or exit_ is not None:
                raise ReplayError(f"Contract candle unavailable at {start}")
            self.boundary_settlement(start, None, record_flat=False, resolvable=False)
            self.ownership.clear()
            for _ in deferred:
                j.record(start, "REJECTED", detail="NO_POSITION")
            self.snapshot(j.record(end, "NO_DATA"), end, None, MarkSource.MARK_UNAVAILABLE, None)
            if trail is not None:
                j.record(end, "REJECTED", detail="NO_POSITION")
            return
        marks = self.marks(trade)
        inputs = self.inputs(start)
        if marks is None or inputs is None:
            if j.position.quantity:
                raise ReplayError(f"Mark, listing or maintenance data unavailable for open position at {start}")
            open_mark = None if marks is None else (marks[0].opening, marks[1])
            self.boundary_settlement(start, open_mark, record_flat=False, resolvable=False)
            self.ownership.clear()
            if entry is not None and marks is not None:
                self.reject_inputs(entry, trade.prices.opening, marks[0].opening)
            elif entry is not None:
                j.record(start, "REJECTED", detail="MARK_UNAVAILABLE")
            for _ in deferred:
                j.record(start, "REJECTED", detail="NO_POSITION")
            seq = j.record(end, "BAR_CLOSE")
            if marks is None:
                self.snapshot(seq, end, None, MarkSource.MARK_UNAVAILABLE, None)
                self.last_close = None
            else:
                prices, _, source = marks
                self.snapshot(seq, end, prices.close, source, self.close_available_at(trade, source))
                self.last_close = end, prices.close, source
            if trail is not None:
                j.record(end, "REJECTED", detail="NO_POSITION")
            return
        mark, source_open, source_close = marks
        filters, tiers = inputs
        pos = j.position
        if pos.quantity and not funding_complete(self.funding, start, start + 1, self.p):
            raise ReplayError(f"Funding settlement continuity unavailable at {start}")
        self.boundary_settlement(start, (mark.opening, source_open), record_flat=True,
                                 resolvable=bool(self.window))

        stop_path = mark if self.p.stop_basis == StopBasis.MARK_PRICE else trade.prices
        open_protection = self.protect(
            start, trade.prices.opening, mark.opening, stop_path.opening, mark.opening, source_open,
            filters, tiers, intrabar=False,
        )
        if exit_ is not None and not open_protection:
            self.exit_intent(exit_, trade.prices.opening, mark.opening, source_open, filters)
        elif exit_ is not None:
            j.record(start, "REJECTED", detail="PROTECTIVE_EXIT_AT_OPEN")
        for pending in deferred:
            if not j.position.quantity or pending.position_epoch != self.position_epoch:
                j.record(start, "REJECTED", detail="TRAIL_POSITION_CLOSED")
            else:
                self.trail(pending.intent, start, pending.closing_price, stop_path.opening, filters)
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
            for f in self.inside_funding.get(start, ()):
                if f.mark is None:
                    raise ReplayError(f"Intrabar funding has no settlement mark at {f.time}")
                # OHLC cannot order the settlement against a protective exit in this bar.
                # Charge adverse funding now; withhold a credit until the bar resolves.
                # Adverse funding reduces margin before the protection check.
                self.post_settlement(f, f.time, f.mark, MarkSource.FUNDING_RECORD,
                                     self.candidates(f), resolvable=True)
            # OHLC supplies a bound, not an intrabar timestamp. Record the outcome
            # at interval end, before close/trailing and the next bar's open.
            self.protect(
                end, adverse_trade, worst_mark, worst_stop, mark.close, source_close,
                filters, tiers, intrabar=True, contract_stop=self.p.stop_basis == StopBasis.CONTRACT_PRICE,
                cost_time=start,
            )
        close_available = self.close_available_at(trade, source_close)
        self.resolve_ownership(end, mark.close, source_close, close_available)
        seq = j.record(end, "BAR_CLOSE")
        self.snapshot(seq, end, mark.close, source_close, close_available)
        self.last_close = end, mark.close, source_close
        if trail is not None and j.position.quantity:
            available = max(close_available, trail.available)
            if available <= end:
                closing_inputs = self.inputs(end)
                if closing_inputs is None:
                    j.record(end, "REJECTED", detail="PIT_INPUTS")
                else:
                    self.trail(trail, end, stop_path.close, stop_path.close, closing_inputs[0])
            else:
                interval = self.plan.interval
                opening = (available + interval - 1) // interval * interval
                if opening < self.end:
                    self.deferred_trails.setdefault(opening, []).append(
                        DeferredTrail(trail, stop_path.close, self.position_epoch),
                    )
                    j.record(end, "TRAIL_DEFERRED", detail=f"next_eligible_open={opening}")
                else:
                    j.record(end, "REJECTED", detail="TRAIL_OUTSIDE_REPLAY")
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

    def candidates(self, event: Funding) -> set[Decimal]:
        """Positions that may have been held at a settlement, given the fills so far.

        A zero window keeps the frozen exact-time priority: no fill is ambiguous.
        """
        held = {self.journal.position.quantity}
        if not self.window:
            return held
        for fill in reversed(self.exposures):
            if fill.latest < event.time - self.window:
                break
            if fill.earliest <= event.time + self.window:
                held |= {fill.before, fill.after}
        return held

    def boundary_settlement(
        self, boundary: int, opening: tuple[Decimal, MarkSource] | None, *, record_flat: bool,
        resolvable: bool,
    ) -> None:
        event = self.boundary_funding.get(boundary)
        if event is None:
            return
        candidates = self.candidates(event)
        if event.mark is not None:
            price, source = event.mark, MarkSource.FUNDING_RECORD
        elif opening is not None:
            price, source = opening
        elif self.last_close is not None and self.last_close[0] == boundary:
            _, price, source = self.last_close
        elif candidates == {ZERO}:
            return
        else:
            raise ReplayError(f"Boundary funding at {event.time} has no valuation mark")
        if candidates == {ZERO}:
            if record_flat:
                snapped = f"SETTLED_AT={event.time}" if event.time != boundary else ""
                self.journal.record(boundary, "FUNDING_FLAT", detail=snapped)
            if resolvable:
                self.ownership.append(_Ownership(event, price, ZERO, ZERO, ZERO))
        else:
            self.post_settlement(event, boundary, price, source, candidates, resolvable=resolvable)

    def post_settlement(
        self, event: Funding, time: int, price: Decimal, source: MarkSource,
        candidates: set[Decimal], *, resolvable: bool,
    ) -> None:
        """Post the most adverse funding among the possible holders; flag any ambiguity.

        While a later fill or this bar's OHLC can still change the holder, only
        min(worst, 0) posts. The rest of a credit is released once the bar resolves.
        """
        current = self.journal.position.quantity
        holder = min(candidates, key=lambda q: (-q * price * event.rate, q != current, q))
        worst = -holder * price * event.rate
        cash = min(worst, ZERO) if resolvable else worst
        detail = [f"SETTLED_AT={event.time}"] if event.time != time else []
        if len(candidates) > 1:
            detail.append("OWNERSHIP_AMBIGUOUS")
        if cash < worst:
            detail.append("CREDIT_WITHHELD")
        seq = self.journal.settle(time, price, cash, holder, ",".join(detail))
        self.funding_observed(event, seq, time, price, source, time)
        if resolvable:
            self.ownership.append(_Ownership(event, price, cash, worst, holder))

    def resolve_ownership(
        self, time: int, mark: Decimal, source: MarkSource, available_at: int,
    ) -> None:
        """At bar end the OHLC fixes every holder; release credits nobody could forfeit."""
        for pending in self.ownership:
            if pending.final > pending.posted:
                seq = self.journal.settle(
                    time, pending.price, pending.final - pending.posted, pending.holder,
                    f"CREDIT_RELEASED,SETTLED_AT={pending.event.time}",
                )
                pending.posted = pending.final
                self.funding_observed(pending.event, seq, time, mark, source, available_at)
        self.ownership.clear()

    def funding_observed(
        self, event: Funding, seq: int, time: int, price: Decimal, source: MarkSource,
        available_at: int | None,
    ) -> None:
        mark_as_of = event.mark_evidence.as_of if event.mark_evidence is not None else event.time
        # Cash is observable no earlier than its posting: sizing and every later
        # snapshot that carries it wait for the same instant (a released credit
        # waits for the bar's OHLC that resolved it).
        self.cash_available_at = max(
            self.cash_available_at, event.evidence.as_of, mark_as_of,
            time if available_at is None else available_at,
        )
        self.snapshot(seq, time, price, source, None if available_at is None else self.cash_available_at)

    def exposure_changed(
        self, earliest: int, latest: int, before: Decimal, time: int, mark: Decimal,
        source: MarkSource, *, observable: bool = True,
    ) -> None:
        """Record a fill and charge any settlement whose holder it makes ambiguous."""
        after = self.journal.position.quantity
        self.exposures.append(_Exposure(earliest, latest, before, after))
        for pending in self.ownership:
            event = pending.event
            if latest < event.time - self.window or earliest > event.time + self.window:
                continue
            holder = min((before, after), key=lambda q: -q * pending.price * event.rate)
            worst = -holder * pending.price * event.rate
            if worst < pending.final:
                pending.final, pending.holder = worst, holder
            if pending.final < pending.posted:
                seq = self.journal.settle(
                    time, pending.price, pending.final - pending.posted, holder,
                    f"OWNERSHIP_ADJUSTMENT,SETTLED_AT={event.time}",
                )
                pending.posted = pending.final
                self.funding_observed(event, seq, time, mark, source, time if observable else None)

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
        if intent.time < self.cash_available_at:
            j.record(intent.time, "REJECTED", detail="FUNDING_CASH_UNAVAILABLE")
            return
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
        if not pos.quantity:
            self.position_epoch += 1
        seq = j.fill(
            intent.time, kind, qty * intent.side, execution, self.p.taker_fee,
            margin=decision.margin, reserve=decision.reserve, stop=stop,
        )
        self.snapshot(seq, intent.time, mark, source, intent.time)
        self.exposure_changed(intent.time, intent.time, pos.quantity, intent.time, mark, source)
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
        self.snapshot(seq, intent.time, mark, source, intent.time)
        self.exposure_changed(intent.time, intent.time, pos.quantity, intent.time, mark, source)

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
        self.snapshot(seq, time, snapshot_mark, source, None if intrabar else time)
        # An intrabar exit may have executed at any instant of its bar.
        earliest = cost_time if intrabar and cost_time is not None else time
        self.exposure_changed(earliest, time, pos.quantity, time, snapshot_mark, source,
                              observable=not intrabar)
        return True

    def trail(
        self, intent: Intent, time: int, closing_price: Decimal, current_price: Decimal, filters: Filters,
    ) -> None:
        pos = self.journal.position
        side = Side.LONG if pos.quantity > ZERO else Side.SHORT
        candidate = stop_price(closing_price, intent.distance, side, filters.tick)
        if intent.side != side:
            reason = "POSITION_SIDE"
        elif not filters.min_price <= candidate <= filters.max_price or candidate <= ZERO:
            reason = "PRICE_TICK"
        elif min(side * (closing_price - candidate), side * (current_price - candidate)) <= ZERO:
            reason = "STOP_IMMEDIATELY_TRIGGERABLE"
        elif side * (candidate - pos.stop) <= ZERO:
            reason = "TRAIL_NOT_FAVORABLE"
        else:
            self.journal.position = replace(pos, stop=candidate)
            self.journal.record(time, "TRAIL_RATCHET", price=candidate)
            return
        # Keep the existing stop and record why the step-wise update was not applied.
        self.journal.record(time, "REJECTED", price=candidate, detail=reason)

    def slippage(self, time: int, *, stress: bool) -> Decimal:
        rate = self.p.stressed_slippage if stress else self.p.slippage
        return execution_slippage(current_spread(self.market, self.inst, time, self.p), rate)
