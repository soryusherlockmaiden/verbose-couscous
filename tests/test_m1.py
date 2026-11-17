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
from acgs.history_io import _capture_observations, load_history
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
)
from acgs.research_io import _policy
from acgs.sim_config import EPOCH, Policy, StopBasis, canonical, digest
from acgs.sim_ledger import MarkSource
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
    return _policy(SETTINGS)


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


def test_t18_near_open_holder_and_t19_unresolved_intrabar_funding_stop(
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
    assert [e.time for e in result.events if e.kind == "FUNDING"] == [late.time]
    data = change_bar(data, 1, prices("100", "101", "92", "95"))
    with pytest.raises(ReplayError, match="Ambiguous intrabar"):
        replay(Market(data), "fixture", config, clock(T + 2 * HOUR), plan(enter(T + HOUR)))


def test_t19_t21_gap_and_missing_mark_or_candle_fail_closed(dataset: Dataset, policy: Policy) -> None:
    dataset = change_bar(dataset, 1, prices("90", "93", "89", "92"))
    result = replay(Market(dataset), "fixture", policy, clock(T + 2 * HOUR), plan(enter()))
    stop = next(e for e in result.events if e.kind == "STOP")
    assert stop.time == T + HOUR and stop.price == D("89.7")
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
    lagged = replace(dataset, candles=tuple(
        replace(b, available=b.end + 1000) if b.start == T and not b.is_mark else b
        for b in dataset.candles
    ))
    run = replay(Market(lagged), "fixture", policy, clock(T + 2 * HOUR), plan(*trade))
    assert not any(e.kind == "TRAIL_RATCHET" for e in run.events)
    assert any(e.detail == "TRAIL_CLOSE_UNAVAILABLE" for e in run.events)


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


def test_t19_t21_mark_retained_when_flat_and_unsupported_tier_aborts_exposure(
    dataset: Dataset, policy: Policy,
) -> None:
    no_filters = Market(replace(dataset, filters=()))
    decision = replay(no_filters, "fixture", policy, clock(T + HOUR), plan(enter()))
    assert any(e.detail == "PIT_INPUTS" for e in decision.events)
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
    market = Market(replace(
        daily_data, filters=(*daily_data.filters,
                             replace(filters(), effective=T, evidence=evidence(T), min_notional=D(50))),
        spreads=(),
    ))
    run_plan = plan(enter(), exit_(T + HOUR))

    def run_decisions() -> tuple[object, object]:
        sim = replay(sim_market, "fixture", policy, clock(T + 2 * HOUR), run_plan)
        report = feasibility(market, "fixture", policy, T, clock(T + DAY))
        return sim, report

    result = run_decisions()
    assert digest(asdict(result[0])) == "3a4cbe6c11e66912fa1da3c56ce59c10d5fb87f5d8bcaa801907d811297f5b01"

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
