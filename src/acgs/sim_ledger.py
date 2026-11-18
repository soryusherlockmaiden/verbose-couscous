"""Simulation-only cash journal and reconciled account snapshots."""

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from acgs.pit import ZERO

# Liquidation clearance is a fee: the exchange keeps the remaining isolated margin.
FEE_KINDS = frozenset({"FEE", "LIQUIDATION_CLEARANCE"})


class MarkSource(StrEnum):
    FLAT = "FLAT"
    MARK_KLINE_OPEN = "MARK_KLINE_OPEN"
    MARK_KLINE_CLOSE = "MARK_KLINE_CLOSE"
    CANDLE_OPEN_PROXY = "CANDLE_OPEN_PROXY"
    CANDLE_CLOSE_PROXY = "CANDLE_CLOSE_PROXY"
    MARK_UNAVAILABLE = "MARK_UNAVAILABLE"
    FUNDING_RECORD = "FUNDING_RECORD"


@dataclass(frozen=True)
class Position:
    quantity: Decimal = ZERO
    basis: Decimal = ZERO
    margin: Decimal = ZERO
    reserve: Decimal = ZERO
    stop: Decimal = ZERO


@dataclass(frozen=True)
class Posting:
    kind: str
    cash: Decimal


@dataclass(frozen=True)
class Event:
    sequence: int
    time: int
    kind: str
    quantity: Decimal
    price: Decimal | None
    postings: tuple[Posting, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class AccountSnapshot:
    sequence: int
    timestamp: int
    position: Position
    wallet: Decimal
    realized: Decimal
    funding: Decimal
    fees: Decimal
    unrealized: Decimal
    equity: Decimal
    available_collateral: Decimal
    mark: Decimal | None
    mark_source: MarkSource
    cycle_equity: Decimal
    trigger_observable: bool
    trigger_available_at: int | None
    run_digest: str

    def observable_by(self, time: int) -> bool:
        """Whether this valuation is available for a decision at this UTC instant."""
        return self.trigger_available_at is not None and self.trigger_available_at <= time


class Journal:
    def __init__(self, initial: Decimal) -> None:
        self.initial = initial
        self.position = Position()
        self.events: list[Event] = []
        self.snapshots: list[AccountSnapshot] = []
        self.wallet = initial
        self.realized = ZERO
        self.funding = ZERO
        self.fees = ZERO
        # Funding credited while exposed belongs to the isolated margin it would forfeit.
        self.credits = ZERO

    def record(
        self, time: int, kind: str, qty: Decimal = ZERO, price: Decimal | None = None,
        postings: tuple[Posting, ...] = (), detail: str = "",
    ) -> int:
        if self.events and time < self.events[-1].time:
            raise ValueError("Nonmonotonic replay event")
        seq = len(self.events) + 1
        self.events.append(Event(seq, time, kind, qty, price, postings, detail))
        for posting in postings:
            self.wallet += posting.cash
            if posting.kind in FEE_KINDS:
                self.fees -= posting.cash
            elif posting.kind == "FUNDING":
                self.funding += posting.cash
            else:
                self.realized += posting.cash
        return seq

    def fill(
        self, time: int, kind: str, delta: Decimal, price: Decimal,
        fee_rate: Decimal, *, margin: Decimal = ZERO, reserve: Decimal = ZERO,
        stop: Decimal = ZERO, detail: str = "", forfeit: Decimal | None = None,
    ) -> int:
        """Post one fill. `forfeit` is the minimum cash loss of a liquidation."""
        pos = self.position
        new_qty = pos.quantity + delta
        fee = abs(delta) * price * fee_rate
        postings: tuple[Posting, ...] = (Posting("FEE", -fee),)
        if not delta or price <= ZERO:
            raise ValueError("Fill requires nonzero quantity and a positive price")
        if pos.quantity * delta >= ZERO:
            if forfeit is not None:
                raise ValueError("Only a closing fill can forfeit isolated margin")
            if not pos.quantity:
                self.credits = ZERO
            basis = (pos.quantity * pos.basis + delta * price) / new_qty
            self.position = Position(new_qty, basis, pos.margin + margin, pos.reserve + reserve, stop)
        else:
            if abs(delta) > abs(pos.quantity) or (forfeit is not None and new_qty):
                raise ValueError("Exit cannot reverse a position; liquidation closes it fully")
            realized = -delta * (price - pos.basis)
            fraction = new_qty / pos.quantity
            self.position = (
                Position(new_qty, pos.basis, pos.margin * fraction, pos.reserve * fraction, pos.stop)
                if new_qty else Position()
            )
            self.credits *= fraction
            postings = (Posting("REALIZED_PNL", realized), *postings)
            # Isolated liquidation never returns margin: the residual is a clearance fee.
            if forfeit is not None and realized - fee + forfeit > ZERO:
                postings = (*postings, Posting("LIQUIDATION_CLEARANCE", -(realized - fee + forfeit)))
        return self.record(time, kind, delta, price, postings, detail)

    def settle(self, time: int, price: Decimal, rate: Decimal) -> int:
        cash = -self.position.quantity * price * rate
        # Charge adverse funding against isolated margin as well as posting the cash.
        # The smaller margin is a protection-model input and is never a second debit.
        if cash < ZERO:
            self.position = replace(self.position, margin=max(ZERO, self.position.margin + cash))
        elif self.position.quantity:
            self.credits += cash
        return self.record(time, "FUNDING", self.position.quantity, price, (Posting("FUNDING", cash),))

    @property
    def collateral(self) -> Decimal:
        return self.wallet - self.position.margin - self.position.reserve

    def snapshot(
        self, sequence: int, time: int, mark: Decimal | None, source: MarkSource,
        close_rate: Decimal, available_at: int | None, run_digest: str, tolerance: Decimal,
    ) -> AccountSnapshot:
        pos = self.position
        if mark is None and pos.quantity:
            raise ValueError("Cannot value an open position without a mark")
        if available_at is not None and available_at < time:
            raise ValueError("Snapshot cannot be observed before its event time")
        unrealized = pos.quantity * (mark - pos.basis) if mark is not None else ZERO
        equity = self.wallet + unrealized
        closing_cost = abs(pos.quantity) * mark * close_rate if mark is not None else ZERO
        if (
            abs(self.wallet - (self.initial + self.realized + self.funding - self.fees)) > tolerance
            or abs(equity - self.wallet - unrealized) > tolerance
        ):
            raise ValueError("Simulated cash/equity reconciliation failed")
        snapshot = AccountSnapshot(
            sequence, time, pos, self.wallet, self.realized, self.funding, self.fees, unrealized,
            equity, self.collateral, mark, source, equity - closing_cost,
            available_at == time, available_at, run_digest,
        )
        self.snapshots.append(snapshot)
        return snapshot

    def verify(self, tolerance: Decimal) -> None:
        """Re-derive every snapshot from the immutable event postings alone.

        The running totals above are maintained by the same code that posts cash,
        so this pass is the independent check that nothing bypassed the event log.
        """
        wallet, realized, funding, fees = self.initial, ZERO, ZERO, ZERO
        totals = {0: (wallet, realized, funding, fees)}
        for index, event in enumerate(self.events, 1):
            if event.sequence != index:
                raise ValueError("Replay event sequence is not contiguous")
            for posting in event.postings:
                wallet += posting.cash
                if posting.kind in FEE_KINDS:
                    fees -= posting.cash
                elif posting.kind == "FUNDING":
                    funding += posting.cash
                else:
                    realized += posting.cash
            totals[index] = (wallet, realized, funding, fees)
        for snap in self.snapshots:
            expected = totals.get(snap.sequence)
            pos = snap.position
            if expected is None or (snap.mark is None and pos.quantity):
                raise ValueError("Snapshot does not follow a recorded event")
            unrealized = pos.quantity * (snap.mark - pos.basis) if snap.mark is not None else ZERO
            stated = (snap.wallet, snap.realized, snap.funding, snap.fees)
            if (
                any(abs(a - b) > tolerance for a, b in zip(stated, expected, strict=True))
                or abs(snap.unrealized - unrealized) > tolerance
                or abs(snap.equity - snap.wallet - unrealized) > tolerance
                or snap.cycle_equity > snap.equity + tolerance
                or (not pos.quantity and snap.equity != snap.wallet)
            ):
                raise ValueError(f"Snapshot {snap.sequence} does not reconcile with event postings")
        if abs(self.wallet - wallet) > tolerance:
            raise ValueError("Final wallet does not reconcile with event postings")
