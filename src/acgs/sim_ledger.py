"""Simulation-only cash journal and reconciled account snapshots."""

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from acgs.pit import ZERO


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
    run_digest: str


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
            if posting.kind == "FEE":
                self.fees -= posting.cash
            elif posting.kind == "FUNDING":
                self.funding += posting.cash
            else:
                self.realized += posting.cash
        return seq

    def fill(
        self, time: int, kind: str, delta: Decimal, price: Decimal,
        fee_rate: Decimal, *, margin: Decimal = ZERO, reserve: Decimal = ZERO,
        stop: Decimal = ZERO, detail: str = "",
    ) -> int:
        pos = self.position
        new_qty = pos.quantity + delta
        fee = abs(delta) * price * fee_rate
        pnl: tuple[Posting, ...] = ()
        if not delta or price <= ZERO:
            raise ValueError("Fill requires nonzero quantity and a positive price")
        if pos.quantity * delta >= ZERO:
            basis = (pos.quantity * pos.basis + delta * price) / new_qty
            self.position = Position(new_qty, basis, pos.margin + margin, pos.reserve + reserve, stop)
        else:
            if abs(delta) > abs(pos.quantity):
                raise ValueError("Exit cannot reverse a position")
            realized = -delta * (price - pos.basis)
            fraction = new_qty / pos.quantity
            self.position = (
                Position(new_qty, pos.basis, pos.margin * fraction, pos.reserve * fraction, pos.stop)
                if new_qty else Position()
            )
            pnl = (Posting("REALIZED_PNL", realized),)
        return self.record(time, kind, delta, price, (*pnl, Posting("FEE", -fee)), detail)

    def settle(self, time: int, price: Decimal, rate: Decimal) -> int:
        cash = -self.position.quantity * price * rate
        # Charge adverse funding against isolated margin as well as posting the cash.
        # The smaller margin is a protection-model input and is never a second debit.
        if cash < ZERO:
            self.position = replace(self.position, margin=max(ZERO, self.position.margin + cash))
        return self.record(time, "FUNDING", self.position.quantity, price, (Posting("FUNDING", cash),))

    @property
    def collateral(self) -> Decimal:
        return self.wallet - self.position.margin - self.position.reserve

    def snapshot(
        self, sequence: int, time: int, mark: Decimal | None, source: MarkSource,
        close_rate: Decimal, observable: bool, run_digest: str, tolerance: Decimal,
    ) -> AccountSnapshot:
        pos = self.position
        if mark is None and pos.quantity:
            raise ValueError("Cannot value an open position without a mark")
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
            equity, self.collateral, mark, source, equity - closing_cost, observable, run_digest,
        )
        self.snapshots.append(snapshot)
        return snapshot
