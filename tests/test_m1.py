"""M1 ROADMAP §4 scenarios and deterministic replay regressions."""

import hashlib
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import Decimal, getcontext, localcontext
from pathlib import Path
from zipfile import ZipFile

import pytest

from acgs.__main__ import main
from acgs.broker_sim import Action, Intent, ReplayError, ReplayPlan, replay
from acgs.clock import FixedClock
from acgs.eligibility import Candidate, Side, evaluate, order_step
from acgs.feasibility import feasibility
from acgs.funding import funding_complete, funding_view
from acgs.history_io import _capture_observations, load_history, number
from acgs.ledger import open_ledger
from acgs.pit import (
    DAY,
    HOUR,
    ZERO,
    Candle,
    Dataset,
    Evidence,
    Filters,
    Funding,
    Instrument,
    Listing,
    Lot,
    Maintenance,
    Market,
    MarkGap,
    Prices,
    Provenance,
    Schedule,
    Spread,
    Tier,
    as_of,
)
from acgs.research_io import _plan, _policy
from acgs.sim_config import EPOCH, Policy, StopBasis, canonical, digest
from acgs.sim_ledger import Journal, MarkSource, Posting
from acgs.universe import snapshot

D = Decimal
T = 80 * DAY
INST = Instrument("1000MOCKUSDT", DAY, 1000)
SETTINGS = Path(__file__).resolve().parents[1] / "config" / "m1.json"


def evidence(time: int = T - DAY, provenance: Provenance = Provenance.VERIFIED) -> Evidence:
    return Evidence(
        "fixture", "test", provenance, time,
        "" if provenance == Provenance.VERIFIED else "Synthetic reconstruction or gap",
    )


def clock(time_ms: int) -> FixedClock:
    return FixedClock(EPOCH + timedelta(milliseconds=time_ms))


def prices(opening: str, high: str, low: str, close: str) -> Prices:
    return Prices(D(opening), D(high), D(low), D(close))


def bar(time_ms: int, p: Prices, *, mark: bool = False, size: int = HOUR, delay: int = 0) -> Candle:
    return Candle(INST, time_ms, time_ms + size, time_ms + size + delay, p, D(1000), D(500),
                  evidence(time_ms + size), mark)


def funding(time_ms: int, rate: str = "0.001", *, hours: int | None = 8, delay: int = 0) -> Funding:
    return Funding(INST, time_ms, D(rate), evidence(time_ms + delay), D(100),
                   hours * HOUR if hours else None)


def filters() -> Filters:
    lot = Lot(D("0.1"), D(100), D("0.1"))
    return Filters(INST, T - DAY, evidence(), lot, lot, D("0.1"), D(5), D("0.1"),
                   D(500), D("0.5"), D("1.5"))


@pytest.fixture
def policy() -> Policy:
    # Exact-clock fixtures: a zero window keeps the frozen tie priority, so event-order
    # tests isolate it. `shipped()` and the released-policy digest cover the 15 s window.
    return replace(_policy(SETTINGS), funding_ownership_window_ms=0)


@pytest.fixture
def dataset() -> Dataset:
    base_prices = prices("100", "101", "99", "100")
    return Dataset(
        (Listing(INST, DAY, evidence(DAY), "TRADING"),),
        tuple(bar(T + i * HOUR, base_prices, mark=mark) for i in range(5) for mark in (False, True)),
        tuple(funding(T + i * HOUR) for i in (-24, -16, -8, 0, 8)),
        (Schedule(INST, T - DAY, evidence(), 8 * HOUR),),
        (filters(),),
        (Maintenance(INST, T - DAY, evidence(), (Tier(D(5000), D("0.005"), ZERO, 20),)),),
        (Spread(INST, evidence(), D(2)),),
    )


def order(**kwargs: object) -> Candidate:
    candidate = Candidate(INST, T, Side.LONG, D(100), D(100), D(2), D(50), D(50), D("0.10"), 5)
    return replace(candidate, **kwargs)


def enter(at: int = T, *, distance: str = "4", qty: str = "1") -> Intent:
    return Intent(at, at - 1, Action.ENTER, Side.LONG, D(distance), D(qty))


def exit_(at: int, qty: str | None = None) -> Intent:
    return Intent(at, at - 1, Action.EXIT, Side.SHORT, ZERO, D(qty) if qty else None)


def plan(*intents: Intent, leverage: int = 5) -> ReplayPlan:
    return ReplayPlan("unapproved-test-v1", INST, T, HOUR, D(50), D("0.12"), leverage, intents)


def change_bar(dataset: Dataset, offset: int, p: Prices, *, mark: bool = False) -> Dataset:
    time_ms = T + offset * HOUR
    candles = tuple(x for x in dataset.candles if (x.start, x.is_mark) != (time_ms, mark))
    return replace(dataset, candles=(*candles, bar(time_ms, p, mark=mark)))


def test_t04_t22_minimum_order_cannot_round_up_and_leverage_does_not_override_risk(
    dataset: Dataset, policy: Policy,
) -> None:
    f = replace(filters(), min_notional=D(101))
    market = Market(replace(dataset, filters=(f,)))
    decision = evaluate(market, order(stop_distance=D(5)), policy)
    assert not decision.executable and {"MIN_NOTIONAL", "RISK_BUDGET"} <= set(decision.reasons)
    assert decision.quantity < D("1.1")
    stronger_leverage = evaluate(market, order(stop_distance=D(5), leverage=10), policy)
    assert not stronger_leverage.executable and "RISK_BUDGET" in stronger_leverage.reasons


def test_t41_margin_cap_binds_and_qty_meets_both_lot_steps(dataset: Dataset, policy: Policy) -> None:
    f = replace(filters(), market_lot=Lot(D("0.15"), D(100), D("0.15")))
    assert order_step(f) == D("0.3")
    decision = evaluate(Market(replace(dataset, filters=(f,))), order(stop_distance=D("0.3")), policy)
    assert decision.executable and decision.binding == "MARGIN"
    assert decision.quantity % f.lot.step == decision.quantity % f.market_lot.step == ZERO
    assert ZERO < decision.risk_utilization < 1 and decision.reserve + decision.margin <= D(50)


def test_t41_percent_price_band_does_not_reject_protective_stop_triggers(
    dataset: Dataset, policy: Policy,
) -> None:
    # BTCUSDT's captured band is 0.97-1.03. It bounds a limit price, not a stopPrice.
    band = replace(filters(), percent_down=D("0.97"), percent_up=D("1.03"))
    market = Market(replace(dataset, filters=(band,)))
    wide = evaluate(market, order(stop_distance=D(5)), policy)
    assert wide.executable and wide.stop == D(95)
    assert "PRICE_TICK" in evaluate(market, order(entry=D("103.1")), policy).reasons
    floor = replace(band, min_price=D(96))
    assert "PRICE_TICK" in evaluate(Market(replace(dataset, filters=(floor,))),
                                    order(stop_distance=D(5)), policy).reasons
    climb = change_bar(change_bar(dataset, 0, prices("100", "112", "99", "110")), 0,
                       prices("100", "112", "99", "110"), mark=True)
    run = replay(Market(replace(climb, filters=(band,))), "fixture", policy, clock(T + 2 * HOUR),
                 plan(enter(distance="5"), Intent(T + HOUR, T + HOUR, Action.TRAIL, Side.LONG, D(6))))
    assert [(e.time, e.price) for e in run.events if e.kind == "TRAIL_RATCHET"] == [(T + HOUR, D(104))]


def test_t33_gap_preserves_risk_and_rounds_stop_toward_actual_fill(
    dataset: Dataset, policy: Policy,
) -> None:
    market = Market(change_bar(dataset, 0, prices("101", "102", "100", "101")))
    run = replay(market, "fixture", policy, clock(T + HOUR), plan(enter(distance="3.25")))
    decision = run.decisions[0]
    assert decision.executable
    assert decision.candidate.entry == D("101.2")
    assert decision.stop == D("98.0")
    assert decision.candidate.entry - decision.stop <= D("3.25")
    assert decision.stressed_loss <= decision.risk_budget


def test_t05_t17_t18_deterministic_cash_funding_and_partial_close(
    dataset: Dataset, policy: Policy,
) -> None:
    settlements = (*dataset.settlements, funding(T + HOUR, hours=1), funding(T + 2 * HOUR, hours=1),
                   funding(T + 3 * HOUR, "0.002", hours=1), funding(T + 4 * HOUR, hours=1))
    market = Market(replace(dataset, settlements=settlements))
    config = replace(policy, fill_fraction=D("0.5"))
    sequence = plan(enter(), enter(T + HOUR), exit_(T + 2 * HOUR, "0.3"), exit_(T + 3 * HOUR))
    end = clock(T + 4 * HOUR)
    result = replay(market, "fixture", config, end, sequence)
    assert result == replay(market, "fixture", config, end, sequence)
    assert [e.kind for e in result.events].count("PARTIAL_FILL") == 2
    # Each partial fill persists the full decision and the revalidated fraction it filled.
    fills = [e.quantity for e in result.events if e.kind == "PARTIAL_FILL"]
    assert [d.quantity for d in result.decisions[1::2]] == fills
    assert all(d.executable for d in result.decisions) and len(result.decisions) == 4
    charges = [e for e in result.events if e.kind == "FUNDING"]
    assert [(e.time, e.quantity) for e in charges] == [
        (T + HOUR, D("0.5")), (T + 2 * HOUR, D("1.0")), (T + 3 * HOUR, D("0.7")),
    ]
    assert next(e for e in result.events if e.time == T).kind == "FUNDING_FLAT"
    for snap in result.snapshots:
        assert snap.wallet == D(50) + snap.realized + snap.funding - snap.fees
        assert snap.equity == snap.wallet + snap.unrealized
    tail = result.snapshots[-1]
    assert tail.position.quantity == ZERO and tail.equity == tail.wallet < D(50)
    assert tail.realized < ZERO and tail.funding == D("-0.29") and tail.fees > ZERO
    assert [(e.time, e.sequence) for e in result.events] == sorted(
        (e.time, e.sequence) for e in result.events
    )


def test_t17_reconciliation_rederives_snapshots_from_immutable_postings() -> None:
    tolerance = D("0.00000001")
    journal = Journal(D(50))
    fill = journal.fill(T, "ENTRY_FILL", D(1), D(100), D("0.0005"), margin=D(10))
    journal.snapshot(fill, T, D(101), MarkSource.MARK_KLINE_OPEN, D("0.0035"), T, "run", tolerance)
    journal.verify(tolerance)
    # Cash moved outside the event log keeps the running totals self-consistent ...
    journal.wallet += 1
    journal.realized += 1
    close = journal.record(T + HOUR, "BAR_CLOSE")
    journal.snapshot(close, T + HOUR, D(101), MarkSource.MARK_KLINE_CLOSE, D("0.0035"), T + HOUR, "run",
                     tolerance)
    # ... but cannot be re-derived from the immutable postings.
    with pytest.raises(ValueError, match="reconcile with event postings"):
        journal.verify(tolerance)
    unrecorded = Journal(D(50))
    unrecorded.record(T, "ADJUSTMENT", postings=(Posting("TEST_ADJUSTMENT", D(1)),))
    unrecorded.events.pop()
    with pytest.raises(ValueError, match="reconcile with event postings"):
        unrecorded.verify(tolerance)


def test_t18_near_open_holder_and_t19_intrabar_funding_beside_possible_stop(
    dataset: Dataset, policy: Policy,
) -> None:
    f = funding(T + HOUR - 1, hours=1)
    early = Market(replace(dataset, settlements=(
        *dataset.settlements, f, funding(T + 2 * HOUR, hours=1),
    )))
    assert not any(e.kind == "FUNDING" for e in replay(
        early, "fixture", policy, clock(T + 2 * HOUR), plan(enter(T + HOUR)),
    ).events)
    late = funding(T + HOUR + 1, hours=1)
    data = replace(dataset, settlements=(*dataset.settlements, late))
    config = replace(policy, funding_jitter_ms=5)
    market = Market(data)
    result = replay(market, "fixture", config, clock(T + 2 * HOUR), plan(enter(T + HOUR)))
    assert [(e.time, e.detail) for e in result.events if e.kind == "FUNDING"] == [(late.time, "")]
    # OHLC cannot order the settlement against the possible stop: the flat holder is a
    # candidate, so adverse funding is charged and favorable funding is omitted.
    stopped = change_bar(data, 1, prices("100", "101", "92", "95"))
    for rate, cash, detail in (("0.001", D("-0.1000"), ""), ("-0.001", ZERO, "CREDIT_WITHHELD")):
        settled = replace(stopped, settlements=(*dataset.settlements, replace(late, rate=D(rate))))
        run = replay(Market(settled), "fixture", config, clock(T + 2 * HOUR), plan(enter(T + HOUR)))
        charge, = (e for e in run.events if e.kind == "FUNDING")
        assert charge.detail == detail and charge.postings[0].cash == cash
        assert [e.kind for e in run.events if e.sequence > charge.sequence][:1] == ["STOP"]
        assert run.snapshots[-1].funding == cash


def shipped(**changes: object) -> Policy:
    """The released policy, with its 15 s funding ownership window."""
    return replace(_policy(SETTINGS), **changes)


@pytest.mark.parametrize("offset", [-1, 0, 1, 15_000])
@pytest.mark.parametrize("opening", [True, False])
@pytest.mark.parametrize("rate", ["0.001", "-0.001"])
def test_t18_ownership_window_charges_adverse_and_omits_favorable_funding(
    dataset: Dataset, offset: int, opening: bool, rate: str,
) -> None:
    policy = shipped(funding_jitter_ms=20_000)
    assert policy.funding_ownership_window_ms == 15_000
    settlement = funding(T + 2 * HOUR + offset, rate, hours=2)
    market = Market(replace(dataset, settlements=(*dataset.settlements, settlement)))
    intents = (enter(T + 2 * HOUR),) if opening else (enter(T + HOUR), exit_(T + 2 * HOUR))
    run = replay(market, "fixture", policy, clock(T + 3 * HOUR), plan(*intents))
    charges = [e for e in run.events if e.kind in {"FUNDING", "FUNDING_FLAT"} and e.time == T + 2 * HOUR]
    # Either holder is possible around the boundary, so the adverse one pays and a
    # favorable credit never reaches the account. The replay itself does not fail.
    adverse = rate == "0.001"
    assert run.snapshots[-1].funding == (D("-0.1000") if adverse else ZERO)
    adjusted = [e for e in charges if e.detail.startswith("OWNERSHIP_ADJUSTMENT")]
    assert bool(adjusted) == (opening and adverse)
    assert all(e.detail.endswith(f"SETTLED_AT={settlement.time}") for e in adjusted)
    posted, = (e for e in charges if e not in adjusted)
    assert (f"SETTLED_AT={settlement.time}" in posted.detail) == bool(offset)
    # A credit to a holder who exits inside the window is withheld, never posted then reversed.
    assert ("CREDIT_WITHHELD" in posted.detail) == (not opening and not adverse)
    assert not any("CREDIT_RELEASED" in e.detail for e in run.events)


@pytest.mark.parametrize("rate", ["0.001", "-0.001"])
@pytest.mark.parametrize("stopped_bar", [1, 2])
def test_t18_intrabar_stop_beside_boundary_settlement_is_conservative(
    dataset: Dataset, rate: str, stopped_bar: int,
) -> None:
    # The stop may execute anywhere in its bar, so within 15 s of a settlement at
    # either bar boundary. That ambiguity is priced, not turned into a failed replay.
    data = change_bar(change_bar(dataset, stopped_bar, prices("100", "101", "90", "92")),
                      stopped_bar, prices("100", "101", "90", "92"), mark=True)
    data = replace(data, settlements=(*data.settlements, funding(T + 2 * HOUR, rate, hours=2)))
    run = replay(Market(data), "fixture", shipped(), clock(T + 3 * HOUR), plan(enter(T + HOUR)))
    stop, = (e for e in run.events if e.kind == "STOP")
    assert stop.time == T + (stopped_bar + 1) * HOUR
    charges = [e for e in run.events if e.kind == "FUNDING"]
    assert all("SETTLED_AT" not in e.detail for e in charges if "ADJUSTMENT" not in e.detail)
    # A stop before the boundary makes the flat account a candidate at posting time; a
    # stop after it reverses only a credit. An adverse charge already posted stands.
    flagged = any("OWNERSHIP" in e.detail or "CREDIT_WITHHELD" in e.detail for e in charges)
    assert flagged == (stopped_bar == 1 or rate == "-0.001")
    assert all(p.cash <= ZERO for e in charges for p in e.postings)
    assert run.snapshots[-1].funding == (D("-0.1000") if rate == "0.001" else ZERO)
    with pytest.raises(ReplayError, match="half a bar"):
        replay(Market(data), "fixture", shipped(), clock(T + 3 * HOUR),
               replace(plan(enter(T + HOUR)), interval=30_000))


@pytest.mark.parametrize("window", [0, 15_000])
def test_t18_published_funding_never_depends_on_later_prices(dataset: Dataset, window: int) -> None:
    """A snapshot at a settlement is the same whatever the bar does afterwards."""
    policy = shipped(funding_ownership_window_ms=window)
    inside = replace(funding(T + HOUR + 30 * 60_000, "-0.001", hours=None), interval_ms=90 * 60_000)
    published = []
    for low in ("99", "90"):
        path = prices("100", "101", low, "100")
        data = change_bar(change_bar(dataset, 1, path), 1, path, mark=True)
        data = replace(data, settlements=(*data.settlements[:4], inside))
        run = replay(Market(data), "fixture", policy, clock(T + 2 * HOUR), plan(enter(T + HOUR)))
        posted = next(e for e in run.events if e.kind == "FUNDING")
        snap = next(s for s in run.snapshots if s.sequence == posted.sequence)
        assert posted.time == inside.time and posted.postings[0].cash == ZERO
        published.append((snap.equity, snap.cycle_equity, snap.observable_by(T + 2 * HOUR - 1)))
        assert not snap.observable_by(inside.time)
        released = [e for e in run.events if "CREDIT_RELEASED" in e.detail]
        # Without a stop the credit is released at bar end, observable only with its OHLC.
        if low == "99":
            release, = released
            assert release.time == T + 2 * HOUR and release.postings[0].cash == D("0.1000")
            snap = next(s for s in run.snapshots if s.sequence == release.sequence)
            assert snap.trigger_available_at == T + 2 * HOUR
        else:
            assert not released and any(e.kind == "STOP" for e in run.events)
    assert published[0] == published[1]
    # A boundary credit next to a stop in its bar is withheld, not posted and reversed.
    path = prices("100", "101", "90", "92")
    data = change_bar(change_bar(dataset, 2, path), 2, path, mark=True)
    data = replace(data, settlements=(*data.settlements, funding(T + 2 * HOUR, "-0.001", hours=2)))
    run = replay(Market(data), "fixture", shipped(), clock(T + 3 * HOUR), plan(enter(T + HOUR)))
    assert [p.cash for e in run.events if e.kind == "FUNDING" for p in e.postings] == [ZERO]
    assert max(s.cycle_equity for s in run.snapshots if s.timestamp >= T + 2 * HOUR) <= max(
        s.cycle_equity for s in run.snapshots if s.timestamp < T + 2 * HOUR
    )


def test_t18_offset_archive_settlement_uses_boundary_mark_without_rest_mark(dataset: Dataset) -> None:
    # Archive rows carry no settlement mark. A 15 ms calc_time offset is a boundary
    # settlement, valued at the opening mark print, not an intrabar event to fail on.
    offset = replace(funding(T + 2 * HOUR + 15, hours=2), mark=None)
    data = replace(dataset, settlements=(*dataset.settlements, offset))
    run = replay(Market(data), "fixture", shipped(), clock(T + 3 * HOUR), plan(enter(T + HOUR)))
    charge, = (e for e in run.events if e.kind == "FUNDING")
    assert charge.time == T + 2 * HOUR and charge.detail == f"SETTLED_AT={offset.time}"
    assert charge.postings[0].cash == -charge.quantity * D(100) * offset.rate
    snap, = (s for s in run.snapshots if s.sequence == charge.sequence)
    assert snap.mark_source == MarkSource.MARK_KLINE_OPEN
    with pytest.raises(ReplayError, match="no settlement mark"):
        replay(Market(data), "fixture", shipped(funding_ownership_window_ms=0), clock(T + 3 * HOUR),
               plan(enter(T + HOUR)))
    # A settlement 1 ms before the horizon still belongs to the replay.
    horizon = replace(funding(T + 2 * HOUR - 1, hours=2), mark=None)
    near_end = replace(dataset, settlements=(*dataset.settlements, horizon))
    tail = replay(Market(near_end), "fixture", shipped(), clock(T + 2 * HOUR), plan(enter(T + HOUR)))
    last, = (e for e in tail.events if e.kind == "FUNDING")
    assert last.time == T + 2 * HOUR and last.detail == f"SETTLED_AT={horizon.time}"
    assert tail.snapshots[-1].mark_source == MarkSource.MARK_KLINE_OPEN


def test_t18_offset_with_primary_mark_posts_once_for_unchanged_holder(dataset: Dataset) -> None:
    policy = _policy(SETTINGS)
    raw = funding(T + 2 * HOUR + 15, delay=5000, hours=2)
    record = replace(raw, mark=D(105), mark_evidence=evidence(raw.time + 6000))
    data = replace(dataset, settlements=(*dataset.settlements, record))
    run = replay(Market(data), "fixture", policy, clock(T + 3 * HOUR), plan(enter(T + HOUR)))
    event, = (e for e in run.events if e.kind == "FUNDING")
    assert event.time == T + 2 * HOUR and event.detail == f"SETTLED_AT={record.time}"
    assert event.postings[0].cash == D("-0.105")
    snap, = (s for s in run.snapshots if s.sequence == event.sequence)
    assert snap.mark_source == MarkSource.FUNDING_RECORD and snap.mark == D(105)
    assert not snap.observable_by(record.time + 5999) and snap.observable_by(record.time + 6000)


def test_t18_funding_cash_cannot_size_entry_before_observation(dataset: Dataset, policy: Policy) -> None:
    payment = funding(T + HOUR, delay=1000, hours=1)
    data = replace(dataset, settlements=(*dataset.settlements, payment))
    run = replay(Market(data), "fixture", policy, clock(T + 2 * HOUR),
                 plan(enter(), exit_(T + HOUR), enter(T + HOUR)))
    assert [e.kind for e in run.events if e.time == T + HOUR] == [
        "BAR_CLOSE", "FUNDING", "EXIT_FILL", "REJECTED",
    ]
    assert any(e.detail == "FUNDING_CASH_UNAVAILABLE" for e in run.events)
    sequences = {e.sequence for e in run.events if e.kind in {"FUNDING", "EXIT_FILL"}}
    delayed = [s.trigger_available_at for s in run.snapshots if s.sequence in sequences]
    assert delayed == [T + HOUR + 1000, T + HOUR + 1000]


def test_t19_t21_gap_and_missing_mark_or_candle_fail_closed(dataset: Dataset, policy: Policy) -> None:
    dataset = change_bar(dataset, 1, prices("90", "93", "89", "92"))
    result = replay(Market(dataset), "fixture", policy, clock(T + 2 * HOUR), plan(enter()))
    stop = next(e for e in result.events if e.kind == "STOP")
    assert stop.time == T + HOUR and stop.price == D("89.7")
    gapped = replay(Market(dataset), "fixture", policy, clock(T + 2 * HOUR),
                    plan(enter(), exit_(T + HOUR)))
    assert [e.detail for e in gapped.events if e.kind == "REJECTED"] == ["PROTECTIVE_EXIT_AT_OPEN"]
    for mark in (False, True):
        incomplete = replace(dataset, candles=tuple(
            b for b in dataset.candles if (b.start, b.is_mark) != (T + HOUR, mark)
        ))
        with pytest.raises(ReplayError, match="unavailable"):
            replay(Market(incomplete), "fixture", policy, clock(T + 2 * HOUR), plan(enter()))
    window = MarkGap(INST, T + HOUR, T + 2 * HOUR, evidence(T, Provenance.UNAVAILABLE))
    source = Market(replace(incomplete, mark_gaps=(window,)))
    result = replay(source, "fixture", replace(policy, allow_documented_mark_proxy=True),
                    clock(T + 2 * HOUR), plan(enter()))
    assert any(s.mark_source == MarkSource.CANDLE_OPEN_PROXY for s in result.snapshots)
    assert result.snapshots[-1].mark_source == MarkSource.CANDLE_CLOSE_PROXY
    strict = replace(policy, allow_documented_mark_proxy=True, allow_reconstructed=False)
    with pytest.raises(ReplayError, match="unavailable"):
        replay(source, "fixture", strict, clock(T + 2 * HOUR), plan(enter()))


def test_t37_trailing_stop_only_at_completed_available_close_and_only_ratchets(
    dataset: Dataset, policy: Policy,
) -> None:
    dataset = change_bar(dataset, 0, prices("100", "104", "99", "103"))
    trade = (
        enter(),
        Intent(T + HOUR, T + HOUR, Action.TRAIL, Side.LONG, D(4)),
        Intent(T + 2 * HOUR, T + 2 * HOUR, Action.TRAIL, Side.LONG, D(5)),
    )
    market = Market(dataset)
    result = replay(market, "fixture", policy, clock(T + 2 * HOUR), plan(*trade))
    assert [(e.time, e.price) for e in result.events if e.kind == "TRAIL_RATCHET"] == [
        (T + HOUR, D(99))
    ]
    # The ratcheted stop at 99 is hit in the next bar, so the later update finds no position.
    assert [(e.time, e.detail) for e in result.events if e.kind == "REJECTED"] == [
        (T + 2 * HOUR, "NO_POSITION")
    ]
    wider = replay(market, "fixture", policy, clock(T + 2 * HOUR),
                   plan(enter(), Intent(T + HOUR, T + HOUR, Action.TRAIL, Side.LONG, D(8))))
    assert [(e.time, e.price, e.detail) for e in wider.events if e.kind == "REJECTED"] == [
        (T + HOUR, D(95), "TRAIL_NOT_FAVORABLE")
    ]
    lagged = replace(dataset, candles=tuple(
        replace(b, available=b.end + 1000) if b.start == T and not b.is_mark else b
        for b in dataset.candles
    ))
    result = replay(Market(lagged), "fixture", policy, clock(T + 3 * HOUR), plan(*trade))
    deferred, = (e for e in result.events if e.kind == "TRAIL_DEFERRED")
    assert deferred.time == T + HOUR
    assert [(e.time, e.price) for e in result.events if e.kind == "TRAIL_RATCHET"] == [
        (T + 2 * HOUR, D(99))
    ]
    first_close = next(s for s in result.snapshots if s.timestamp == T + HOUR)
    assert first_close.trigger_available_at == T + HOUR + 1000
    assert not first_close.observable_by(T + HOUR + 999)
    assert first_close.observable_by(T + HOUR + 1000)
    flat = replay(market, "fixture", policy, clock(T + 2 * HOUR), plan(*trade[1:]))
    assert [e.detail for e in flat.events if e.kind == "REJECTED"] == ["NO_POSITION"] * 2


@pytest.mark.parametrize("path,reason", [
    (prices("98", "101", "98", "100"), "STOP_IMMEDIATELY_TRIGGERABLE"),
    (prices("90", "101", "89", "100"), "TRAIL_POSITION_CLOSED"),
])
def test_t37_delayed_trailing_yields_to_open_gap(
    dataset: Dataset, policy: Policy, path: Prices, reason: str,
) -> None:
    data = change_bar(dataset, 0, prices("100", "104", "99", "103"))
    data = change_bar(data, 2, path)
    close = T + HOUR
    trailing = Intent(close, close + 1000, Action.TRAIL, Side.LONG, D(4))
    result = replay(Market(data), "fixture", policy, clock(T + 3 * HOUR), plan(enter(), trailing))
    assert not any(e.kind == "TRAIL_RATCHET" for e in result.events)
    assert any(e.time == T + 2 * HOUR and e.detail == reason for e in result.events)
    if reason == "TRAIL_POSITION_CLOSED":
        stop, = (e for e in result.events if e.kind == "STOP")
        refused, = (e for e in result.events if e.detail == reason)
        assert stop.time == refused.time and stop.sequence < refused.sequence


def test_t37_delayed_trailing_stays_with_original_position(dataset: Dataset, policy: Policy) -> None:
    data = change_bar(dataset, 0, prices("100", "104", "99", "103"))
    close = T + HOUR
    trailing = Intent(close, close + 1000, Action.TRAIL, Side.LONG, D(4))
    run = replay(
        Market(data), "fixture", policy, clock(T + 3 * HOUR),
        plan(enter(), exit_(close), enter(close), trailing),
    )
    assert [e.kind for e in run.events].count("ENTRY_FILL") == 2
    assert not any(e.kind == "TRAIL_RATCHET" for e in run.events)
    assert any(e.time == T + 2 * HOUR and e.detail == "TRAIL_POSITION_CLOSED" for e in run.events)


@pytest.mark.parametrize("stop_basis", [StopBasis.CONTRACT_PRICE, StopBasis.MARK_PRICE])
def test_t38_mark_price_can_liquidate_before_stop(dataset: Dataset, policy: Policy, stop_basis: StopBasis) -> None:
    dataset = change_bar(dataset, 0, prices("100", "101", "87", "99"), mark=True)
    dataset = change_bar(dataset, 0, prices("100", "101", "99", "100"))
    run = replay(Market(dataset), "fixture", replace(policy, stop_basis=stop_basis),
                 clock(T + 2 * HOUR), plan(enter(), enter(T + HOUR), leverage=10))
    fills = [e for e in run.events if e.kind in {"LIQUIDATION", "STOP"}]
    assert len(fills) == 1 and fills[0].kind == "LIQUIDATION"
    assert not next(s for s in run.snapshots if s.sequence == fills[0].sequence).trigger_observable
    assert any(e.detail == "LIQUIDATION_LATCH" for e in run.events)


def test_t38_isolated_liquidation_forfeits_remaining_margin_and_funding_credit(
    dataset: Dataset, policy: Policy,
) -> None:
    # The mark crosses maintenance just past the boundary. The modeled fill alone
    # would hand back part of the isolated margin, which Binance never does.
    credit = funding(T + HOUR, "-0.001", hours=1)
    path = prices("100", "101", "91.4", "99")
    data = change_bar(change_bar(dataset, 1, path, mark=True), 1, path)
    data = replace(data, settlements=(*data.settlements, credit))
    run = replay(Market(data), "fixture", policy, clock(T + 2 * HOUR),
                 plan(enter(distance="4"), leverage=10))
    entry = next(e for e in run.events if e.kind == "ENTRY_FILL")
    liquidation, = (e for e in run.events if e.kind == "LIQUIDATION")
    before = next(s for s in run.snapshots if s.sequence == entry.sequence)
    after = next(s for s in run.snapshots if s.sequence == liquidation.sequence)
    received = next(e for e in run.events if e.kind == "FUNDING").postings[0].cash
    assert received == D("0.1")
    margin = before.position.margin
    clearance, = (p.cash for p in liquidation.postings if p.kind == "LIQUIDATION_CLEARANCE")
    assert clearance < ZERO
    assert before.wallet + received - after.wallet == margin + received
    assert after.fees - before.fees == -sum(
        p.cash for p in liquidation.postings if p.kind != "REALIZED_PNL"
    )
    assert after.equity == after.wallet == D(50) + after.realized + after.funding - after.fees
    deeper = prices("100", "101", "80", "99")
    gapped = replace(change_bar(change_bar(dataset, 1, deeper, mark=True), 1, deeper),
                     settlements=data.settlements)
    severe = replay(Market(gapped), "fixture", policy, clock(T + 2 * HOUR),
                    plan(enter(distance="4"), leverage=10))
    assert not any(p.kind == "LIQUIDATION_CLEARANCE" for e in severe.events for p in e.postings)
    assert severe.snapshots[-1].wallet < after.wallet


def test_t19_delisting_boundary_refuses_open_position(dataset: Dataset, policy: Policy) -> None:
    delisted = Listing(INST, T + 30 * 60_000, evidence(T - HOUR), "DELISTED")
    market = Market(replace(dataset, listings=(*dataset.listings, delisted)))
    with pytest.raises(ReplayError, match="listing boundary"):
        replay(market, "fixture", policy, clock(T + HOUR), plan(enter()))
    # Evidence recorded after the bar still marks an exchange event the position lived through.
    for status, effective in (("CLOSE_ONLY", T + 30 * 60_000), ("DELISTED", T + HOUR)):
        late = Listing(INST, effective, evidence(T + 2 * HOUR), status)
        market = Market(replace(dataset, listings=(*dataset.listings, late)))
        assert evaluate(market, order(), policy).executable
        with pytest.raises(ReplayError, match="listing boundary"):
            replay(market, "fixture", policy, clock(T + HOUR), plan(enter()))


def test_t19_t21_mark_retained_when_flat_and_unsupported_tier_aborts_exposure(
    dataset: Dataset, policy: Policy,
) -> None:
    no_filters = Market(replace(dataset, filters=()))
    decision = replay(no_filters, "fixture", policy, clock(T + HOUR), plan(enter()))
    assert any(e.detail == "PIT_INPUTS" for e in decision.events)
    refused, = decision.decisions
    assert refused.reasons == ("MISSING_PIT_FILTER",) and refused.filter_evidence is None
    assert refused.provenance == Provenance.UNAVAILABLE
    snap = decision.snapshots[-1]
    assert snap.equity == snap.wallet == D(50)
    assert snap.mark == D(100) and snap.mark_source == MarkSource.MARK_KLINE_CLOSE
    extreme = change_bar(dataset, 0, prices("100", "5200", "99", "100"), mark=True)
    with pytest.raises(ReplayError, match="tier coverage"):
        replay(Market(extreme), "fixture", policy, clock(T + HOUR), plan(enter()))


def test_t36_t42_causal_interval_change_default_and_missing_record(dataset: Dataset, policy: Policy) -> None:
    prior = tuple(funding(T + n * HOUR) for n in (-16, -8, 0))
    changed = tuple(funding(T + n * HOUR, hours=4, delay=500) for n in (4, 8, 12))
    source = Market(replace(dataset, settlements=(*prior, *changed), schedules=()))
    before = funding_view(source, INST, T + 4 * HOUR, policy)
    after = funding_view(source, INST, T + 4 * HOUR + 500, policy)
    assert before.interval_ms == 8 * HOUR and after.interval_ms == 4 * HOUR
    assert before.evidence.provenance == after.evidence.provenance == Provenance.RECONSTRUCTED
    assert funding_complete(source.settlements[INST], T, T + 12 * HOUR, policy)
    assert not funding_complete((*prior, changed[0], changed[2]), T, T + 12 * HOUR + 500, policy)
    premium = Schedule(INST, T, evidence(T), next_time=T + 4 * HOUR)
    default = Schedule(INST, T, evidence(T, Provenance.RECONSTRUCTED), 4 * HOUR)
    missing = Market(replace(dataset, settlements=(), schedules=(premium,)))
    view = funding_view(missing, INST, T, policy)
    assert view.next_time == T + 4 * HOUR and view.interval_ms is None
    explicit = Market(replace(dataset, settlements=(), schedules=(default, replace(premium, effective=T - 1))))
    observed = funding_view(explicit, INST, T, policy)
    assert observed.interval_ms == 4 * HOUR and observed.evidence.provenance == Provenance.RECONSTRUCTED
    calm = prices("100", "101", "99", "100")
    extra = tuple(bar(T + i * HOUR, calm, mark=m) for i in range(5, 9) for m in (False, True))
    gap = Market(replace(dataset, candles=(*dataset.candles, *extra),
                         settlements=tuple(funding(T + i * HOUR) for i in (-16, -8, 0, 16))))
    with pytest.raises(ReplayError, match="Funding"):
        replay(gap, "fixture", policy, clock(T + 9 * HOUR), plan(enter()))


def daily(dataset: Dataset) -> Dataset:
    bars = []
    for day in range(16, 80):
        price = D(111) - day / D(5)
        p = Prices(price, price + D("0.5"), price - D("0.5"), price)
        bars.extend(bar(day * DAY, p, size=DAY, mark=m, delay=1000) for m in (False, True))
    settlements = tuple(funding(i, delay=5000) for i in range(16 * DAY, T + HOUR, 8 * HOUR))
    return replace(dataset, candles=tuple(bars), settlements=settlements)


def test_t02_t03_t35_future_inputs_and_relisting_never_change_previous_decision(
    dataset: Dataset, policy: Policy,
) -> None:
    baseline = daily(dataset)
    now = clock(T + 60_000)
    universe = snapshot(Market(baseline), now, policy)
    assert universe.ranks[0].reason == "CANDIDATE" and universe.ranks[0].momentum < ZERO
    new = replace(INST, onboard=T + DAY)
    future = replace(
        baseline,
        listings=(*baseline.listings, Listing(INST, T + DAY, evidence(T + DAY), "DELISTED"),
                  Listing(new, T + DAY, evidence(T + DAY), "TRADING")),
        candles=(*baseline.candles, bar(T, prices("90", "500", "80", "400"), size=DAY)),
        filters=(*baseline.filters, replace(filters(), effective=T, evidence=evidence(T + HOUR))),
        settlements=(*baseline.settlements, funding(T + 4 * HOUR, hours=4)),
    )
    assert snapshot(Market(future), now, policy) == universe
    assert evaluate(Market(future), order(), policy) == evaluate(Market(baseline), order(), policy)
    later = snapshot(Market(future), clock(T + DAY), policy)
    assert {r.instrument.id: r.reason for r in later.ranks} == {
        INST.id: "NOT_TRADING", new.id: "INSUFFICIENT_HISTORY",
    }
    assert new.id != INST.id and new.multiplier == 1000
    absent = Market(replace(baseline, filters=()))
    assert "MISSING_PIT_FILTER" in evaluate(absent, order(), policy).reasons
    gap = Market(replace(baseline, candles=baseline.candles[:-4] + baseline.candles[-2:]))
    assert snapshot(gap, now, policy).ranks[0].reason == "INSUFFICIENT_HISTORY"


def test_t01_t20_t23_runtime_clock_report_and_config_identity(
    dataset: Dataset, policy: Policy, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sim_market = Market(dataset)
    daily_data = daily(dataset)
    market_data = replace(
        daily_data, filters=(*daily_data.filters,
                             replace(filters(), effective=T, evidence=evidence(T), min_notional=D(50))),
        spreads=(),
    )
    market = Market(market_data)
    run_plan = plan(enter(), exit_(T + HOUR))

    def run_decisions() -> tuple[object, object]:
        sim = replay(sim_market, "fixture", policy, clock(T + 2 * HOUR), run_plan)
        report = feasibility(market, "fixture", policy, T, clock(T + DAY))
        return sim, report

    result = run_decisions()
    assert digest(asdict(result[0])) == "5b48562772fa9164fbc3ddd209e052cd88ea995ae5c2121512161adb5aa3ed30"
    # The released policy prices the entry at the settlement instant conservatively.
    released = replay(sim_market, "fixture", _policy(SETTINGS), clock(T + 2 * HOUR), run_plan)
    assert digest(asdict(released)) == "5f96eccf5c5c571e88f94377237b4c586c56717c7a02ed4e0ca69d50bf2d8290"
    assert [e.detail for e in released.events if e.kind == "FUNDING"] == [
        f"OWNERSHIP_ADJUSTMENT,SETTLED_AT={T}"
    ]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Decision code accessed the wall clock")

    class NoClock(datetime):
        now = classmethod(forbidden)
        utcnow = classmethod(forbidden)
        today = classmethod(forbidden)

    with monkeypatch.context() as m, localcontext():
        getcontext().prec = 7
        import datetime as datetime_module

        m.setattr(datetime_module, "datetime", NoClock)
        for name in ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"):
            m.setattr(time, name, forbidden)
        for name, module in tuple(sys.modules.items()):
            if name.startswith("acgs.") and getattr(module, "datetime", None) is datetime:
                m.setattr(module, "datetime", NoClock)
        assert canonical(result) == canonical(run_decisions())
    _, summary = result
    assert isinstance(summary, dict) and len(summary["scenarios"]) == 72
    decisions = summary["decisions"]
    assert any(d["executable"] for d in decisions)
    assert any(not d["executable"] for d in decisions)
    assert {d["provenance"] for d in decisions} == {Provenance.RECONSTRUCTED}
    assert {d["candidate"]["side"] for d in decisions} == {Side.SHORT}
    assert summary["status"] == "ASSESSABLE" and summary["version"] == "m1-feasibility-v6"
    # Every attempt missing a PIT filter is inconclusive; assessable rejections are no-trade.
    blind = feasibility(Market(replace(daily_data, filters=())), "fixture", policy, T, clock(T + DAY))
    assert {d["provenance"] for d in blind["decisions"]} == {Provenance.UNAVAILABLE}
    assert {d["assessable"] for d in blind["decisions"]} == {False}
    assert {d["assessable"] for d in decisions} == {True}
    assert blind["status"] == "INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE"
    strict = Market(replace(market_data, filters=(replace(filters(), effective=T, evidence=evidence(T),
                                                          min_notional=D(100_000)),)))
    rejected = feasibility(strict, "fixture", policy, T, clock(T + DAY))
    assert not any(d["executable"] for d in rejected["decisions"])
    assert rejected["status"] == "NO_TRADE_IN_DECLARED_WINDOW"

    config = tmp_path / "paper.toml"
    config.write_text('mode = "PAPER"\n', encoding="utf-8")
    assert main(["--config", str(config), "replay", "--manifest", "missing", "--assets", ".",
                 "--policy", str(SETTINGS), "--start", "unused", "--end", "unused",
                 "--plan", "missing"]) == 2


def test_t01_ingest_integrity_cli_and_immutable_outputs(tmp_path: Path) -> None:
    asset = tmp_path / "market.zip"
    with ZipFile(asset, "w") as archive:
        archive.writestr("fixture.csv", f"{T},100,102,99,101,10,{T+HOUR-1},1000,10,0,0,0\n")
    source = {
        "version": "m1-history-v1", "ingested_at": T + 2 * HOUR, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000, "instruments": [{"symbol": INST.symbol, "onboard": INST.onboard,
                                                  "multiplier": INST.multiplier}],
        "listings": [{"id": INST.id, "effective": INST.onboard, "status": "TRADING",
                      "evidence": dict(source="fixture", method="test", as_of=INST.onboard,
                                       provenance="VERIFIED")}],
        "assets": [{"id": INST.id, "source": "fixture", "path": asset.name, "start": T,
                    "end": T + HOUR, "kind": "klines", "interval_ms": HOUR,
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(source), encoding="utf-8")
    out = tmp_path / "historical.json"
    args = ["historical", "--manifest", str(manifest), "--assets", str(tmp_path), "--out", str(out)]
    assert main(args) == main(args) == 0
    loaded = load_history(manifest, tmp_path)
    assert loaded.market.history(INST, T + HOUR, HOUR, 1) == ()
    assert len(loaded.market.history(INST, T + HOUR + 1000, HOUR, 1)) == 1
    source["funding_delay_ms"] = 6000
    manifest.write_text(json.dumps(source), encoding="utf-8")
    assert main(args) == 1
    source["assets"] *= 2
    manifest.write_text(json.dumps(source), encoding="utf-8")
    assert main(args) == 1
    asset.write_bytes(b"wrong checksum")
    assert main(args) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["dataset_digest"] == loaded.digest


def test_t23_empty_attempts_do_not_establish_no_trade(dataset: Dataset, policy: Policy) -> None:
    start = 17 * DAY
    report = feasibility(Market(daily(dataset)), "fixture", policy, start, clock(start + DAY))
    assert report["status"] == "INCONCLUSIVE_NO_ASSESSABLE_ATTEMPTS"
    assert report["decisions"] == []
    assert report["universe_exclusions"] == {"INSUFFICIENT_HISTORY": 1}
    assert all(row["executable_rate"] is None for row in report["scenarios"].values())


def test_t23_stale_filters_do_not_establish_no_trade(dataset: Dataset, policy: Policy) -> None:
    old = replace(filters(), effective=T - 3 * DAY, evidence=evidence(T - 3 * DAY))
    data = replace(daily(dataset), filters=(old,))
    report = feasibility(Market(data), "fixture", policy, T, clock(T + DAY))
    assert report["status"] == "INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE"
    assert report["decisions"]
    assert {d["reasons"] for d in report["decisions"]} == {("STALE_METADATA",)}
    # The stale filter is still verified evidence; it just cannot decide this attempt.
    assert {d["provenance"] for d in report["decisions"]} == {Provenance.VERIFIED}
    assert {d["assessable"] for d in report["decisions"]} == {False}
    assert all(row["assessable"] == 0 for row in report["scenarios"].values())


def test_t18_archive_mark_enrichment_requires_exact_settlement_match(tmp_path: Path) -> None:
    archive = tmp_path / "funding.zip"
    time = T + 15
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("funding.csv", f"calc_time,funding_interval_hours,last_funding_rate\n"
                        f"{time},8,0.001\n")
    row = {"symbol": INST.symbol, "fundingTime": time, "fundingRate": "0.001", "markPrice": "105"}
    mark = tmp_path / "funding.json"
    mark.write_text(json.dumps([row]), encoding="utf-8")
    manifest = {
        "version": "m1-history-v1", "ingested_at": T + HOUR, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000,
        "instruments": [{"symbol": INST.symbol, "onboard": INST.onboard, "multiplier": 1000}],
        "listings": [{"id": INST.id, "effective": INST.onboard, "status": "TRADING",
                      "evidence": dict(source="fixture", method="test", as_of=INST.onboard,
                                       provenance="VERIFIED")}],
        "assets": [
            {"id": INST.id, "source": source, "path": file.name, "start": T, "end": T + HOUR,
             "kind": kind, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
            for file, kind, source in (
                (archive, "fundingRate", "archive-source"),
                (mark, "fundingMarks", "rest-source"),
            )
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    attached, = load_history(path, tmp_path).market.settlements[INST]
    assert attached.time == time and attached.rate == D("0.001") and attached.interval_ms == 8 * HOUR
    assert attached.evidence.source == "archive-source"
    assert attached.mark == D(105) and attached.mark_evidence is not None
    assert attached.mark_evidence.source == "rest-source" and attached.mark_evidence.as_of == time + 5000
    for mutation in ({"fundingTime": time + 1}, {"fundingRate": "0.002"}, {"markPrice": None}):
        mark.write_text(json.dumps([row | mutation]), encoding="utf-8")
        manifest["assets"][1]["sha256"] = hashlib.sha256(mark.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match an archived instrument"):
            load_history(path, tmp_path)


def test_t02_t42_m0_capture_read_only_and_funding_absence_withdraws_adjustment(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    fixture = (Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_bytes()
    stamps = ("2026-11-17T15:11:58.957281+00:00", "2026-11-17T15:12:00+00:00",
              "2026-11-18T15:12:00+00:00")
    adjusted = json.dumps([{
        "symbol": "BTCUSDT", "fundingIntervalHours": 4,
        "adjustedFundingRateCap": "0.006", "adjustedFundingRateFloor": "-0.006",
    }]).encode()
    with open_ledger(path) as conn, conn:
        for idx, (kind, payload) in enumerate((
            ("exchangeInfo", fixture), ("fundingInfo", adjusted), ("fundingInfo", b"[]"),
        )):
            conn.execute(
                """INSERT INTO capture_attempts
                    (source_url, slot, requested_at, received_at, status, http_status,
                     payload, sha256, provenance)
                    VALUES (?, ?, ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED')""",
                (f"https://fapi.binance.com/fapi/v1/{kind}", str(idx), stamps[idx], stamps[idx],
                 payload, hashlib.sha256(payload).hexdigest()),
            )
    cutoff = 1_795_200_000_000
    *_, hashes = _capture_observations(path, {"BTCUSDT"}, cutoff)
    manifest = tmp_path / "capture.json"
    manifest.write_text(json.dumps({
        "version": "m1-history-v1", "ingested_at": cutoff, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000, "instruments": [], "listings": [], "assets": [],
        "capture": {"symbols": ["BTCUSDT"], "cutoff": cutoff, "digest": digest(hashes)},
    }), encoding="utf-8")
    before = path.read_bytes()
    market = load_history(manifest, tmp_path, path).market
    assert path.read_bytes() == before
    inst = next(iter(market.listings))
    first_filter, = market.filters[inst]
    assert first_filter.effective == 1_794_928_318_958
    first, second = market.schedules[inst]
    config = _policy(SETTINGS)
    assert funding_view(market, inst, first.effective, config).interval_ms == 4 * HOUR
    assert funding_view(market, inst, second.effective, config).interval_ms is None


def test_t01_offline_inputs_require_exact_decimal_text(tmp_path: Path) -> None:
    # A JSON float imports its binary expansion: 0.1 would authorize slightly more than 10%.
    assert number("0.10") == D("0.10") and number(5) == D(5)
    for value in (0.1, True, None, "NaN", "Infinity"):
        with pytest.raises(ValueError):
            number(value)
    raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
    path = tmp_path / "policy.json"
    for key, value in (("taker_fee", 0.0005), ("ign_risks", [0.08, "0.10"]), ("ign_risks", "0.10")):
        path.write_text(json.dumps({**raw, key: value}), encoding="utf-8")
        with pytest.raises(ValueError):
            _policy(path)
    sample = {
        "candidate_version": "decimal-text-v1", "instrument_id": INST.id, "interval_ms": HOUR,
        "equity": "50", "risk_pct": "0.10", "leverage": 10,
        "intents": [{"time": "1970-03-22T00:00:00Z", "available": "1970-03-21T23:59:59Z",
                     "action": "ENTER", "side": "LONG", "distance": "2"}],
    }
    assert _plan(sample, INST, T).risk_pct == D("0.10")
    intent = sample["intents"][0]
    for change in ({"risk_pct": 0.1}, {"equity": 50.0}, {"leverage": True},
                   {"interval_ms": 86_400_000.0}, {"candidate_version": 1},
                   {"intents": [{**intent, "distance": 2000.5}]}):
        with pytest.raises(ValueError):
            _plan({**sample, **change}, INST, T)


def test_t02_latest_exchange_info_withdraws_missing_filters_and_symbols(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    no_minimum = json.loads(json.dumps(fixture))
    for item in no_minimum["symbols"]:
        item["filters"] = [f for f in item["filters"] if f["filterType"] != "MIN_NOTIONAL"]
    delisted = {**fixture, "symbols": [s for s in fixture["symbols"] if s["symbol"] != "BTCUSDT"]}
    path = tmp_path / "capture.sqlite3"
    stamps = ("2026-11-17T00:00:00+00:00", "2026-11-17T06:00:00+00:00", "2026-11-17T12:00:00+00:00")
    with open_ledger(path) as conn, conn:
        for idx, body in enumerate((fixture, no_minimum, delisted)):
            payload = json.dumps(body).encode()
            conn.execute(
                """INSERT INTO capture_attempts
                    (source_url, slot, requested_at, received_at, status, http_status,
                     payload, sha256, provenance)
                    VALUES (?, ?, ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED')""",
                ("https://fapi.binance.com/fapi/v1/exchangeInfo", str(idx), stamps[idx], stamps[idx],
                 payload, hashlib.sha256(payload).hexdigest()),
            )
    listings, captured, *_ = _capture_observations(path, {"BTCUSDT"}, 1_900_000_000_000)
    market = Market(Dataset(tuple(listings), (), filters=tuple(captured)))
    inst, = market.listings
    first, second, third = (1_794_873_600_000, 1_794_895_200_000, 1_794_916_800_000)
    policy = _policy(SETTINGS)

    def reasons(time: int) -> set[str]:
        candidate = Candidate(inst, time, Side.LONG, D(90000), D(90000), D(1000), D(50), D(50),
                              D("0.10"), 10)
        return set(evaluate(market, candidate, policy).reasons)

    assert as_of(market.filters[inst], first + 1).evidence.provenance == Provenance.VERIFIED
    assert "UNVERIFIED_CRITICAL_FILTER" not in reasons(first + 1)
    withdrawn = as_of(market.filters[inst], second + 1)
    assert withdrawn.evidence.provenance == Provenance.UNAVAILABLE
    assert "UNVERIFIED_CRITICAL_FILTER" in reasons(second + 1)
    absent = as_of(market.listings[inst], third + 1)
    assert absent.status == "ABSENT_FROM_EXCHANGE_INFO" and not absent.tradable
    assert "MISSING_PIT_FILTER" in reasons(third + 1)


def test_t41_liquidation_step_down_also_covers_the_mark(dataset: Dataset, policy: Policy) -> None:
    # The maintenance boundary at the mark lands exactly on 0.85 units (step 0.05).
    lot = Lot(D("0.05"), D(100), D("0.05"))
    tiers = (Tier(D(45), D("0.005"), ZERO, 20), Tier(D(5000), D("0.09"), D("3.825"), 20))
    market = Market(replace(
        dataset, filters=(replace(filters(), lot=lot, market_lot=lot),),
        maintenance=(Maintenance(INST, T - DAY, evidence(), tiers),),
    ))
    decision = evaluate(market, order(mark=D(95), stop_distance=D("0.5"), leverage=10, risk_pct=D(1)), policy)
    assert dict(decision.caps)["LIQUIDATION"] == D("0.85")
    assert decision.executable and decision.binding == "LIQUIDATION" and decision.quantity == D("0.80")


def test_t03_reconstructed_spread_is_refused_when_reconstruction_is_disallowed(
    dataset: Dataset, policy: Policy,
) -> None:
    strict = replace(policy, allow_reconstructed=False)
    rebuilt = Spread(INST, evidence(provenance=Provenance.RECONSTRUCTED), D(2))
    decision = evaluate(Market(replace(dataset, spreads=(rebuilt,))), order(), strict)
    assert not decision.executable and "SPREAD_COST_UNCERTAIN" in decision.reasons
    assert evaluate(Market(replace(dataset, spreads=(rebuilt,))), order(), policy).executable
    data = replace(daily(dataset), spreads=(replace(rebuilt, evidence=evidence(T, Provenance.RECONSTRUCTED)),),
                   filters=(replace(filters(), effective=T, evidence=evidence(T), min_notional=D(50)),))
    report = feasibility(Market(data), "fixture", strict, T, clock(T + DAY))
    assert report["decisions"] and not any(d["executable"] or d["assessable"] for d in report["decisions"])
    assert report["status"] == "INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE"


def lagged(dataset: Dataset) -> Dataset:
    """Every candle closes 1,000 ms before it is published, as in the pinned archives."""
    return replace(dataset, candles=tuple(replace(b, available=b.end + 1000) for b in dataset.candles))


def test_t18_released_credit_cannot_size_or_show_before_its_bar_is_published(dataset: Dataset) -> None:
    inside = replace(funding(T + HOUR + 30 * 60_000, "-0.001", hours=None), interval_ms=90 * 60_000)
    data = replace(lagged(dataset), settlements=(*dataset.settlements[:4], inside))
    run = replay(Market(data), "fixture", shipped(), clock(T + 3 * HOUR),
                 plan(enter(T + HOUR, qty="0.5"), enter(T + 2 * HOUR, qty="0.5")))
    release, = (e for e in run.events if "CREDIT_RELEASED" in e.detail)
    assert release.time == T + 2 * HOUR
    # The second entry would size on the released credit before it is observable.
    assert any(e.time == T + 2 * HOUR and e.detail == "FUNDING_CASH_UNAVAILABLE" for e in run.events)
    later = [s for s in run.snapshots if s.sequence >= release.sequence and s.trigger_available_at is not None]
    assert later and all(s.trigger_available_at >= T + 2 * HOUR + 1000 for s in later)


@pytest.mark.parametrize("recorded_mark", [None, D(101)])
@pytest.mark.parametrize("rate", ["-0.001", "0.001"])
def test_t01_longer_horizon_publishes_the_same_values_at_a_shared_timestamp(
    dataset: Dataset, recorded_mark: Decimal | None, rate: str,
) -> None:
    boundary = T + 2 * HOUR
    before = replace(funding(boundary - 1, rate, hours=2), mark=recorded_mark)
    gap = change_bar(dataset, 2, prices("104", "105", "103", "104"), mark=True)
    data = replace(lagged(gap), settlements=(*dataset.settlements[:4], before))

    def published(hours: int) -> list[tuple[object, ...]]:
        run = replay(Market(data), "fixture", shipped(), clock(T + hours * HOUR), plan(enter(T + HOUR)))
        return [(s.wallet, s.cycle_equity, s.mark, s.mark_source, s.trigger_available_at)
                for s in run.snapshots if s.timestamp <= boundary]

    short, long = published(2), published(3)
    assert short == long
    end = replay(Market(data), "fixture", shipped(), clock(boundary), plan(enter(T + HOUR)))
    settled, = (e for e in end.events if e.kind == "FUNDING")
    price = recorded_mark if recorded_mark is not None else D(104)
    assert settled.price == price and settled.postings[0].cash == min(ZERO, -price * D(rate))
    assert ("CREDIT_WITHHELD" in settled.detail) == (D(rate) < ZERO)


def test_t18_terminal_close_fallback_obeys_candle_availability(dataset: Dataset) -> None:
    boundary = T + 2 * HOUR
    before = replace(funding(boundary - 1, "0.001", hours=2), mark=None)
    delayed = lagged(dataset)
    data = replace(delayed, candles=tuple(b for b in delayed.candles if b.start < boundary),
                   settlements=(*dataset.settlements[:4], before))
    run = replay(Market(data), "fixture", shipped(), clock(boundary), plan(enter(T + HOUR)))

    settled, = (e for e in run.events if e.kind == "FUNDING")
    assert settled.postings[0].cash == D("-0.1000")
    snap = run.snapshots[-1]
    assert snap.sequence == settled.sequence and snap.mark_source == MarkSource.MARK_KLINE_CLOSE
    assert not snap.observable_by(boundary)
    assert snap.trigger_available_at == boundary + 1000


def test_t02_first_capture_withdraws_manifest_filters_and_listings(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    btc = Instrument("BTCUSDT", 1_567_965_300_000)
    manifest_listing = Listing(btc, btc.onboard, evidence(btc.onboard), "TRADING")
    lot = Lot(D("0.001"), D(1000), D("0.001"))
    manifest_filter = Filters(btc, 1_794_800_000_000, evidence(1_794_800_000_000), lot, lot, D("0.1"),
                              D(50), D("556.8"), D(4_529_764), D("0.97"), D("1.03"))
    policy = _policy(SETTINGS)
    receipt = 1_794_873_600_000
    no_minimum = json.loads(json.dumps(fixture))
    for item in no_minimum["symbols"]:
        item["filters"] = [f for f in item["filters"] if f["filterType"] != "MIN_NOTIONAL"]
    delisted = {**fixture, "symbols": []}
    for name, body in (("no_minimum", no_minimum), ("delisted", delisted)):
        path = tmp_path / f"{name}.sqlite3"
        with open_ledger(path) as conn, conn:
            payload = json.dumps(body).encode()
            conn.execute(
                """INSERT INTO capture_attempts
                    (source_url, slot, requested_at, received_at, status, http_status,
                     payload, sha256, provenance)
                    VALUES (?, '0', ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED')""",
                ("https://fapi.binance.com/fapi/v1/exchangeInfo", "2026-11-17T00:00:00+00:00",
                 "2026-11-17T00:00:00+00:00", payload, hashlib.sha256(payload).hexdigest()),
            )
        listings, captured, *_ = _capture_observations(
            path, {"BTCUSDT"}, 1_900_000_000_000, (manifest_listing,),
        )
        market = Market(Dataset((manifest_listing, *listings), (), filters=(manifest_filter, *captured)))
        candidate = Candidate(btc, receipt + 1, Side.LONG, D(90000), D(90000), D(1000), D(50), D(50),
                              D("0.10"), 10)
        before = evaluate(market, replace(candidate, time=receipt - 1), policy)
        after = evaluate(market, candidate, policy)
        assert "MISSING_PIT_FILTER" not in before.reasons
        assert "UNVERIFIED_CRITICAL_FILTER" not in before.reasons
        expected = "UNVERIFIED_CRITICAL_FILTER" if name == "no_minimum" else "MISSING_PIT_FILTER"
        assert expected in after.reasons


def save_exchange_info(db: Path, time: int, body: dict[str, object]) -> str:
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    stamp = clock(time).now().isoformat()
    payload = json.dumps(body).encode()
    sha = hashlib.sha256(payload).hexdigest()
    with open_ledger(db) as conn, conn:
        conn.execute(
            """INSERT INTO capture_attempts
                (source_url, slot, requested_at, received_at, status, http_status,
                 payload, sha256, provenance)
                VALUES (?, ?, ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED')""",
            (url, str(time), stamp, stamp, payload, sha),
        )
    return digest((url, stamp, sha))


@pytest.mark.parametrize("late_notice", [False, True])
def test_t35_capture_preserves_an_existing_delisting_boundary(tmp_path: Path, late_notice: bool) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    row = next(r for r in fixture["symbols"] if r["symbol"] == "BTCUSDT")
    old = Instrument("BTCUSDT", row["onboardDate"])
    receipt = 1_794_928_320_000
    delisted = receipt - 2 * DAY
    new = replace(old, onboard=receipt - DAY)
    db = tmp_path / "capture.sqlite3"
    captured = {**fixture, "symbols": [{**row, "onboardDate": new.onboard}]}
    hashes = [save_exchange_info(db, time, captured) for time in (receipt, receipt + DAY)]
    initial = (
        Listing(old, old.onboard, evidence(old.onboard), "TRADING"),
        Listing(old, delisted, evidence(receipt + DAY if late_notice else delisted), "DELISTED"),
        Listing(new, new.onboard, evidence(new.onboard), "TRADING"),
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(canonical({
        "version": "m1-history-v1", "ingested_at": receipt + DAY,
        "candle_delay_ms": 1000, "funding_delay_ms": 5000,
        "instruments": [asdict(old), asdict(new)],
        "listings": [
            {**asdict(listing), "id": listing.instrument.id} for listing in initial
        ],
        "assets": [],
        "capture": {"symbols": ["BTCUSDT"], "cutoff": receipt + DAY, "digest": digest(hashes)},
    }), encoding="utf-8")

    market = load_history(manifest, tmp_path, db).market
    absent = tuple(row for row in market.listings[old] if row.status == "ABSENT_FROM_EXCHANGE_INFO")
    assert tuple(row for row in market.listings[old] if row not in absent) == initial[:2]
    assert tuple(row.effective for row in absent) == ((receipt,) if late_notice else ())
    assert as_of(market.listings[new], receipt + DAY).tradable


@pytest.mark.parametrize("tick", [D("0.25"), D("0.50")])
def test_t02_filter_withdrawal_carries_no_unobserved_tick(
    tmp_path: Path, dataset: Dataset, tick: Decimal,
) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    row = next(r for r in fixture["symbols"] if r["symbol"] == "BTCUSDT")
    captured = {**fixture, "symbols": [{
        **row, "symbol": INST.symbol, "onboardDate": INST.onboard,
        "filters": [flt for flt in row["filters"] if flt["filterType"] != "MIN_NOTIONAL"],
    }]}
    db = tmp_path / "capture.sqlite3"
    save_exchange_info(db, T, captured)
    observed_later = replace(filters(), effective=T - HOUR, evidence=evidence(T + HOUR), tick=tick)
    listings, withdrawn, *_ = _capture_observations(db, {INST.symbol}, T, dataset.listings)
    market = Market(replace(dataset, listings=(*dataset.listings, *listings),
                            filters=(observed_later, *withdrawn)))

    run = replay(market, "fixture", shipped(), clock(T + HOUR), plan(enter()))
    decision, = run.decisions
    assert not decision.executable and "UNVERIFIED_CRITICAL_FILTER" in decision.reasons
    assert decision.candidate.entry == D(100)
    assert as_of(market.filters[INST], T + HOUR).evidence.provenance == Provenance.UNAVAILABLE


def test_t21_t23_proxy_mark_without_reconstruction_is_never_executable(dataset: Dataset, policy: Policy) -> None:
    data = daily(dataset)
    candles = tuple(c for c in data.candles if not c.is_mark)
    gap = MarkGap(INST, 16 * DAY, T, evidence(16 * DAY, Provenance.UNAVAILABLE))
    data = replace(data, candles=candles, mark_gaps=(gap,), filters=(
        replace(filters(), effective=T, evidence=evidence(T), min_notional=D(50)),
    ))
    proxied = replace(policy, allow_documented_mark_proxy=True)
    allowed = feasibility(Market(data), "fixture", proxied, T, clock(T + DAY))
    assert allowed["status"] == "ASSESSABLE"
    strict = replace(proxied, allow_reconstructed=False)
    report = feasibility(Market(replace(data, spreads=dataset.spreads)), "fixture", strict, T, clock(T + DAY))
    rows = report["decisions"]
    assert rows and all(d["mark_source"] == "CANDLE_CLOSE_PROXY" for d in rows)
    assert not any(d["executable"] or d["assessable"] for d in rows)
    assert all("RECONSTRUCTION_DISALLOWED" in d["assessment_reasons"] for d in rows)
    assert all(row["executable"] == row["assessable"] == 0 for row in report["scenarios"].values())
    assert report["status"] == "INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE"


@pytest.mark.parametrize("low", ["99", "90"])
@pytest.mark.parametrize("short_bar", [False, True])
def test_t21_intrabar_settlement_valuation_waits_for_its_bar(dataset: Dataset, low: str, short_bar: bool) -> None:
    # Full OHLC can rule out an exit, but cannot order an exit against settlement.
    if short_bar:
        policy = shipped(funding_jitter_ms=30_000)
        settled = funding(T + HOUR + 20_000, "0.0001", hours=1)
        following: tuple[Funding, ...] = (funding(T + 2 * HOUR + 20_000, "0.0001", hours=1),)
    else:
        policy = shipped()
        settled = replace(funding(T + HOUR + 30 * 60_000, "0.0001", hours=None), interval_ms=90 * 60_000)
        following = ()
    settled = replace(settled, mark=D(104))
    path = prices("100", "105", low, "100")
    data = replace(change_bar(change_bar(dataset, 1, path), 1, path, mark=True),
                   settlements=(*dataset.settlements[:4], settled, *following))
    data = lagged(data)
    run = replay(Market(data), "fixture", policy, clock(T + 3 * HOUR),
                 plan(enter(T + HOUR, distance="4", qty="0.5"), enter(T + 2 * HOUR, distance="4", qty="0.5")))
    posted = next(e for e in run.events if e.kind == "FUNDING")
    snap = next(s for s in run.snapshots if s.sequence == posted.sequence)
    assert posted.time == settled.time and snap.position.quantity
    assert not snap.observable_by(T + 2 * HOUR)
    assert snap.trigger_available_at == (T + 2 * HOUR + 1000 if low == "99" else None)
    # The debit is certain whatever the order, so it never blocks the next entry.
    assert not any(e.detail == "FUNDING_CASH_UNAVAILABLE" for e in run.events)


@pytest.mark.parametrize(("trade_low", "mark_low", "exit_kind"), [
    ("90", "99", "STOP"),
    ("99", "76", "LIQUIDATION"),
])
def test_t21_ambiguous_settlement_cannot_trigger_recovery(
    dataset: Dataset, trade_low: str, mark_low: str, exit_kind: str,
) -> None:
    settlements = tuple(
        replace(funding(T + offset * HOUR + 20_000, "0.0001"), mark=D(104))
        for offset in (-24, -16, -8, 0, 8)
    )
    trade = change_bar(dataset, 0, prices("100", "105", trade_low, "100"))
    data = change_bar(trade, 0, prices("100", "105", mark_low, "100"), mark=True)
    market = Market(lagged(replace(data, settlements=settlements)))
    run = replay(market, "fixture", shipped(), clock(T + HOUR),
                 replace(plan(enter()), equity=D(97)))
    funding_event, = (e for e in run.events if e.kind == "FUNDING")
    snap, = (s for s in run.snapshots if s.sequence == funding_event.sequence)

    assert funding_event.postings[0].cash == D("-0.01040")
    assert snap.cycle_equity > D(100) and snap.trigger_available_at is None
    assert any(e.kind == exit_kind for e in run.events)
    assert all(s.cycle_equity < D(100) for s in run.snapshots if s.observable_by(T + HOUR + 1000))


def test_t21_intrabar_valuation_waits_for_funding_that_can_affect_protection(dataset: Dataset) -> None:
    path = prices("100", "105", "99", "100")
    candles = tuple(bar(T, path, mark=mark, size=DAY, delay=1000) for mark in (False, True))
    settlements = tuple(
        funding(T + offset * HOUR, "0.0001", delay=9 * HOUR if offset == 16 else 0)
        for offset in (-24, -16, -8, 0, 8, 16, 24)
    )
    market = Market(replace(dataset, candles=candles, settlements=settlements))
    run = replay(market, "fixture", shipped(), clock(T + DAY), replace(plan(enter()), interval=DAY))
    sequences = {e.sequence for e in run.events if e.kind == "FUNDING" and T < e.time < T + DAY}
    valuations = [s for s in run.snapshots if s.sequence in sequences]

    assert len(valuations) == 2
    assert all(s.trigger_available_at == T + 25 * HOUR for s in valuations)


def test_t02_capture_absence_overrides_earlier_status_learned_later(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    row = next(r for r in fixture["symbols"] if r["symbol"] == "BTCUSDT")
    btc = Instrument("BTCUSDT", row["onboardDate"])
    receipt, learned = 1_794_873_600_000, 1_794_960_000_000
    late = Listing(btc, btc.onboard, evidence(learned), "TRADING")
    relisted = Instrument("BTCUSDT", receipt + DAY)
    future = Listing(relisted, relisted.onboard, evidence(receipt), "TRADING")
    path = tmp_path / "capture.sqlite3"
    save_exchange_info(path, receipt, {
        **fixture, "symbols": [{**row, "symbol": "ETHUSDT", "baseAsset": "ETH"}],
    })
    listings, *_ = _capture_observations(path, {"BTCUSDT"}, 1_900_000_000_000, (late, future))
    absent, = listings
    assert absent.instrument == btc and absent.effective == receipt
    market = Market(Dataset((late, future, *listings), ()))
    baseline = Market(Dataset((future,), ()))
    # Adding the future notice cannot introduce its epoch into the earlier universe.
    assert snapshot(market, clock(learned - 1), shipped()) == snapshot(baseline, clock(learned - 1), shipped())
    assert absent.evidence.as_of == learned
    # The later-learned TRADING row is known from `learned`, yet the absence still wins.
    assert as_of(market.listings[btc], learned + 1).status == "ABSENT_FROM_EXCHANGE_INFO"
    # An epoch that starts after the capture is not withdrawn by it.
    assert as_of(market.listings[relisted], relisted.onboard + 1).tradable


def test_t02_pending_absence_does_not_replace_a_known_capture_status(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    row = next(r for r in fixture["symbols"] if r["symbol"] == "BTCUSDT")
    btc = Instrument("BTCUSDT", row["onboardDate"])
    receipt = 1_794_873_600_000
    learned = receipt + DAY
    older = Listing(btc, receipt - 2 * HOUR, evidence(receipt - 2 * HOUR), "TRADING")
    late = Listing(btc, receipt + HOUR // 2, evidence(learned), "TRADING")
    db = tmp_path / "capture.sqlite3"
    save_exchange_info(db, receipt - HOUR, {**fixture, "symbols": [{**row, "status": "CLOSE_ONLY"}]})
    absent = {**fixture, "symbols": [{**row, "symbol": "ETHUSDT", "baseAsset": "ETH"}]}
    save_exchange_info(db, receipt + HOUR, absent)
    save_exchange_info(db, receipt + 2 * HOUR, absent)

    def universe(*prior: Listing) -> Market:
        listings, *_ = _capture_observations(db, {"BTCUSDT"}, receipt + 2 * HOUR, prior)
        return Market(Dataset((*prior, *listings), ()))

    before_notice = clock(learned - 1)
    complete = universe(older, late)
    assert snapshot(complete, before_notice, shipped()) == snapshot(universe(older), before_notice, shipped())
    assert as_of(complete.listings[btc], learned).status == "ABSENT_FROM_EXCHANGE_INFO"


def test_t01_historical_coverage_separates_filter_snapshots_from_withdrawals(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "exchangeInfo.json").read_text(encoding="utf-8"))
    incomplete = json.loads(json.dumps(fixture))
    for item in incomplete["symbols"]:
        item["filters"] = [f for f in item["filters"] if f["filterType"] != "MIN_NOTIONAL"]
    db = tmp_path / "capture.sqlite3"
    receipt = 1_794_928_320_000
    hashes = [save_exchange_info(db, receipt, fixture), save_exchange_info(db, receipt + HOUR, incomplete)]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "m1-history-v1", "ingested_at": receipt + DAY, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000, "instruments": [], "listings": [], "assets": [],
        "capture": {"symbols": ["BTCUSDT"], "cutoff": receipt + DAY, "digest": digest(hashes)},
    }), encoding="utf-8")
    out = tmp_path / "historical.json"
    assert main(["historical", "--manifest", str(manifest), "--assets", str(tmp_path),
                 "--capture-db", str(db), "--out", str(out)]) == 0
    coverage, = json.loads(out.read_text(encoding="utf-8"))["coverage"].values()
    assert (coverage["filter_observations"], coverage["filter_snapshots"], coverage["filter_withdrawals"]) == (2, 1, 1)
