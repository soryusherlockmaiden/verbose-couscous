import hashlib
import json
import os
import runpy
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from m2_fixture import (
    DAY_START,
    END,
    HOLDOUT_START,
    START,
    archive,
    candles,
    make_fixture,
    write_json,
)

from acgs.__main__ import main as m1_main
from acgs.history_io import load_history, read_history_asset, read_json
from acgs.history_seal import AccessRefused, Window, utc_ms
from acgs.ledger import open_ledger
from acgs.m2_coverage import coverage
from acgs.m2_data import fetch_inputs, load_research_inputs
from acgs.m2_io import main
from acgs.market_data import PublicResponse
from acgs.pit import DAY, HOUR
from acgs.research_io import _policy
from acgs.sim_config import EPOCH, digest

REPO = Path(__file__).resolve().parents[1]
POLICY = REPO / "config" / "m1.json"
RESERVE_START = utc_ms("2025-01-01T00:00:00Z")
RESERVE_END = utc_ms("2026-01-01T00:00:00Z")
HOLDOUT_PURPOSE = "m2-prospective-holdout"


@pytest.fixture
def paths(tmp_path):
    make_fixture(tmp_path)
    return tmp_path / "m2.json", tmp_path / "manifest.json", tmp_path / "assets"


def cli(paths, purpose, command, *, start=START, end=END):
    config, manifest, root = paths
    args = ["--config", str(config), "--manifest", str(manifest),
            "--assets", str(root), "--purpose", purpose, command]
    if command == "coverage":
        args.extend(["--policy", str(POLICY),
                     "--start", (EPOCH + timedelta(milliseconds=start)).isoformat(),
                     "--end", (EPOCH + timedelta(milliseconds=end)).isoformat()])
    return main(args)


def pin(paths, config, manifest):
    config_path, manifest_path, _ = paths
    config["access"]["approvals"]["development"]["manifest_sha256"] = digest(manifest)
    write_json(config_path, config)
    write_json(manifest_path, manifest)


_watched_root: Path | None = None


def _audit(event: str, args: tuple[object, ...]) -> None:
    if _watched_root is None:
        return
    if event in {"socket.connect", "urllib.Request"}:
        raise AssertionError(f"Attempted network access: {event}")
    if event == "open" and isinstance(args[0], str | bytes):
        if Path(os.path.abspath(os.fsdecode(args[0]))).is_relative_to(_watched_root):
            raise AssertionError(f"Attempted payload open: {args[0]}")


sys.addaudithook(_audit)


@contextmanager
def forbid_io(root: Path) -> Iterator[None]:
    # Audit hooks are permanent. Activate the guard only around the operation under test.
    global _watched_root
    _watched_root = root.resolve()
    try:
        yield
    finally:
        _watched_root = None


@pytest.mark.parametrize("case", ["direct", "dependency", "funding_dependency"])
def test_t15_seal_refuses_research_and_coverage_before_payload_read(paths, capsys, case):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    manifest = read_json(manifest_path)
    held_out = archive(
        root, "klines", "1h", "2000-01-04", HOLDOUT_START, HOLDOUT_START + 24 * HOUR,
        candles(HOLDOUT_START, HOUR, [0]),
    )
    manifest["assets"].append(held_out)
    config["access"]["approvals"]["development"]["decision_window"]["end"] = HOLDOUT_START
    pin(paths, config, manifest)
    start = HOLDOUT_START - HOUR if case == "dependency" else HOLDOUT_START
    end = HOLDOUT_START if case == "dependency" else HOLDOUT_START + HOUR
    expected = "dependency exceeds" if case == "dependency" else "research cutoff"
    if case == "funding_dependency":
        start, end = HOLDOUT_START - 3 * HOUR, HOLDOUT_START - 2 * HOUR
        expected = "fundingRate dependency exceeds"
    with forbid_io(root):
        with pytest.raises(AccessRefused, match=expected):
            load_research_inputs(config_path, manifest_path, root, "development", Window(start, end))
        assert cli(paths, "development", "coverage", start=start, end=end) == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("status", ["DRAFT", "FROZEN"])
def test_t15_direct_holdout_role_is_refused_in_every_m2_entry_point(paths, capsys, status):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    config["protocol_status"] = status
    write_json(config_path, config)
    with forbid_io(root):
        with pytest.raises(AccessRefused, match="Holdout is sealed"):
            load_research_inputs(
                config_path, manifest_path, root, "holdout",
                Window(HOLDOUT_START, HOLDOUT_START + HOUR),
            )
        for command in ("fetch", "coverage"):
            assert cli(paths, "holdout", command) == 1
            assert "Holdout is sealed" in capsys.readouterr().err


@pytest.mark.parametrize("case", [
    "bad_digest", "bad_pin", "overlap_klines", "overlap_markPriceKlines",
    "overlap_fundingRate", "wrong_interval", "alias",
])
def test_t15_fetch_preflights_entire_manifest_and_load_reads_nothing(
    paths, capsys, case,
):
    config_path, manifest_path, root = paths
    config, manifest = map(read_json, paths[:2])
    first = root / manifest["assets"][0]["path"]
    first.unlink()  # A valid first asset would reach the network without full preflight.
    last = manifest["assets"][-1]
    expected = "Invalid asset pin"
    if case == "bad_digest":
        manifest["ingested_at"] = "2000-02-02T00:00:00Z"
        write_json(manifest_path, manifest)
        expected = "approved digest mismatch"
    elif case == "bad_pin":
        last["sha256"] = "not-a-pin"
    elif case.startswith("overlap_"):
        # The M1 row coverage itself ends before holdout. The URL spans January.
        kind = case.removeprefix("overlap_")
        interval = None if kind == "fundingRate" else "1h"
        rows = candles(DAY_START, HOUR, [0]) if interval else [[DAY_START, 1, "0.001"]]
        monthly = archive(
            root, kind, interval, "2000-01", DAY_START, DAY_START + HOUR,
            rows, period="monthly",
        )
        manifest["assets"].append(monthly)
        expected = "Asset bundle exceeds"
    elif case == "wrong_interval":
        manifest["assets"][1]["interval_ms"] = 24 * HOUR
        expected = "interval differs"
    else:
        last["path"] = "../holdout.zip"
        expected = "path must match"
    if case != "bad_digest":
        pin(paths, config, manifest)
    with forbid_io(root):
        assert cli(paths, "development", "fetch") == 1
        with pytest.raises(AccessRefused, match=expected):
            load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    assert expected in capsys.readouterr().err


def test_draft_without_approvals_refuses_fetch_and_coverage(paths, capsys):
    config_path, _, root = paths
    config = read_json(config_path)
    config["access"] = {"holdout": None, "research_cutoff": None, "approvals": {}}
    write_json(config_path, config)
    with forbid_io(root):
        assert cli(paths, "development", "fetch") == 1
        assert cli(paths, "development", "coverage") == 1
    assert "dataset approval is OPEN" in capsys.readouterr().err


def test_t15_approved_manifest_still_requires_an_explicit_research_cutoff(paths, capsys):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    config["access"]["research_cutoff"] = None
    write_json(config_path, config)
    expected = "research cutoff or dataset approval is OPEN"
    with forbid_io(root):
        with pytest.raises(AccessRefused, match=expected):
            load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
        for command in ("fetch", "coverage"):
            assert cli(paths, "development", command) == 1
            assert expected in capsys.readouterr().err


def test_fetch_checks_pins_and_reuses_verified_local_assets(paths):
    config, manifest_path, root = paths
    manifest = read_json(manifest_path)
    entry = manifest["assets"][-1]
    target = root / entry["path"]
    raw = target.read_bytes()
    target.unlink()
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return PublicResponse(200, raw)

    result = fetch_inputs(config, manifest_path, root, "development", fetch=fake_fetch)
    assert calls == [entry["source"]]
    assert result == {
        "manifest_sha256": digest(manifest),
        "total_bytes": sum(entry["size_bytes"] for entry in manifest["assets"]),
        "fetched_bytes": len(raw),
    }
    assert target.read_bytes() == raw
    assert fetch_inputs(config, manifest_path, root, "development", fetch=fake_fetch)["fetched_bytes"] == 0
    assert calls == [entry["source"]]
    target.write_bytes(b"!" * len(raw))
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_research_inputs(config, manifest_path, root, "development", Window(START, END))
    target.unlink()
    with pytest.raises(ValueError, match="pinned size or checksum"):
        fetch_inputs(config, manifest_path, root, "development",
                     fetch=lambda _: PublicResponse(200, b"!" * len(raw)))
    assert not target.exists()


def test_audit_and_manifest_reproduce_and_report_gaps(paths, tmp_path, capsys):
    assert cli(paths, "development", "coverage") == 0
    first = capsys.readouterr().out
    second = tmp_path / "second"
    make_fixture(second)
    second_paths = second / "m2.json", second / "manifest.json", second / "assets"
    assert second_paths[1].read_bytes() == paths[1].read_bytes()
    assert cli(second_paths, "development", "coverage") == 0
    repeated = capsys.readouterr().out
    assert hashlib.sha256(first.encode()).digest() == hashlib.sha256(repeated.encode()).digest()
    result = json.loads(first)
    epoch = result["epochs"][0]
    assert result["asset_count"] == 7
    assert result["total_bytes"] == sum(p.stat().st_size for p in paths[2].rglob("*.zip"))
    assert epoch["candle_streams"]["klines:15m"]["longest_contiguous_bars"] == 2
    assert [START + HOUR // 2, START + 3 * HOUR // 4] in (
        epoch["candle_streams"]["klines:15m"]["missing_ranges"]
    )
    assert epoch["metadata_without_observations"] == ["filters", "maintenance", "spreads", "schedules"]
    assert epoch["metadata"]["listings"][0]["evidence"]["provenance"] == "RECONSTRUCTED"
    assert epoch["funding"]["missing_intrabar_settlement_marks"]["1h"] == [{
        "settled_at": START + HOUR + 60_000,
        "affected_bar": [START + HOUR, END],
    }]
    assert not epoch["funding"]["settlement_continuity_on_decision_window"]


def test_operator_settlement_mark_sidecar_is_local_and_matched(paths, capsys):
    config_path, manifest_path, root = paths
    config, manifest = map(read_json, paths[:2])
    end = START + 24 * HOUR
    time = START + HOUR + 60_000
    symbol = manifest["instruments"][0]["symbol"]
    relative = f"local/fundingMarks/{symbol}-{START}-{end}.json"
    target = root / relative
    target.parent.mkdir(parents=True)
    manifest["assets"].append({
        "kind": "fundingMarks",
        "id": manifest["listings"][0]["id"],
        "start": START,
        "end": end,
        "source": "https://fapi.binance.com/fapi/v1/fundingRate?"
                  f"symbol={symbol}&startTime={START}&endTime={end - 1}&limit=1000",
        "path": relative,
    })

    def record_mark(rate):
        write_json(target, [{"fundingTime": time, "symbol": symbol,
                             "fundingRate": rate, "markPrice": "102"}])
        raw = target.read_bytes()
        manifest["assets"][-1].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        pin(paths, config, manifest)

    def unexpected(url):
        pytest.fail(f"Made a request for operator-supplied data: {url}")

    record_mark("0.001")
    assert fetch_inputs(config_path, manifest_path, root, "development", fetch=unexpected)["fetched_bytes"] == 0
    assert cli(paths, "development", "coverage") == 0
    funding = json.loads(capsys.readouterr().out)["epochs"][0]["funding"]
    assert funding["missing_intrabar_settlement_marks"]["1h"] == []
    assert funding["settlement_marks"][0]["effective"] == time

    record_mark("0.002")
    with pytest.raises(ValueError, match="does not match"):
        load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    target.unlink()
    with pytest.raises(FileNotFoundError):
        fetch_inputs(config_path, manifest_path, root, "development", fetch=unexpected)



def test_legacy_history_entry_point_refuses_m2_manifest_before_payload_read(paths):
    _, manifest, root = paths
    with forbid_io(root), pytest.raises(ValueError, match="Unknown historical dataset schema"):
        load_history(manifest, root)


def test_t15_legacy_schema_and_cli_refuse_sealed_assets_before_any_payload_read(paths, capsys):
    _, manifest_path, root = paths
    manifest = read_json(manifest_path)
    manifest["version"] = "m1-history-v1"
    manifest.pop("m2_purpose")
    manifest["ingested_at"] = RESERVE_END
    sealed = archive(root, "klines", "1h", "2025-01-01", RESERVE_START,
                     RESERVE_START + DAY, candles(RESERVE_START, HOUR, [0]))
    manifest["assets"].append(sealed)
    write_json(manifest_path, manifest)
    base_args = ["--manifest", str(manifest_path), "--assets", str(root)]
    window_args = ["--policy", str(POLICY), "--start", "2024-12-30T00:00:00Z",
                   "--end", "2025-01-01T00:00:00Z"]
    with forbid_io(root):
        with pytest.raises(AccessRefused, match="Source bundle overlaps"):
            read_history_asset(root, sealed)
        with pytest.raises(AccessRefused, match="Source bundle overlaps"):
            load_history(manifest_path, root)
        for command, extra in (
            ("historical", []), ("feasibility", window_args),
            ("replay", [*window_args, "--plan", "unused"]),
        ):
            assert m1_main([command, *base_args, *extra]) == 1
            assert "Source bundle overlaps" in capsys.readouterr().err

    manifest["assets"].pop()
    write_json(manifest_path, manifest)
    window_args[-2:] = ["--end", "2025-01-02T00:00:00Z"]
    with forbid_io(root):
        for command, extra in (
            ("feasibility", window_args), ("replay", [*window_args, "--plan", "unused"]),
        ):
            assert m1_main([command, *base_args, *extra]) == 1
            assert "M1 research request overlaps" in capsys.readouterr().err


def test_t15_legacy_monthly_fetch_preflights_a_straddling_bundle(paths, tmp_path, monkeypatch):
    _, manifest_path, root = paths
    record = tmp_path / "seal.json"
    write_json(record, {"version": "history-seal-v1", "ranges": [
        {"purpose": "m2-retrospective-reserve", "start": "2024-12-15T00:00:00Z", "end": RESERVE_START},
        {"purpose": "m2-retrospective-reserve", "start": RESERVE_START, "end": RESERVE_END},
    ]})
    monkeypatch.setattr("acgs.history_seal.RECORD_PATH", record)
    manifest = read_json(manifest_path)
    manifest["version"] = "m1-history-v1"
    manifest.pop("m2_purpose")
    first = archive(root, "klines", "1h", "2000-01", START, START + HOUR,
                    candles(START, HOUR, [0]), period="monthly")
    start = utc_ms("2024-12-01T00:00:00Z")
    straddling = archive(root, "klines", "1h", "2024-12", start, start + HOUR,
                        candles(start, HOUR, [0]), period="monthly")
    manifest["assets"] = [first, straddling]
    manifest["ingested_at"] = RESERVE_END
    write_json(manifest_path, manifest)
    (root / first["path"]).unlink()  # Would GET before encountering the second entry.
    script = REPO / "evidence" / "m1" / "fetch.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(manifest_path), str(root)])
    with forbid_io(root), pytest.raises(SystemExit, match="Source bundle overlaps"):
        runpy.run_path(str(script), run_name="__main__")


def test_t15_settlement_query_cannot_hide_a_sealed_tail(paths):
    _, _, root = paths
    declared_start = RESERVE_START - HOUR
    entry = {
        "kind": "fundingMarks",
        "id": f"FIXTUREUSDT@{declared_start}",
        "start": declared_start,
        "end": RESERVE_START,
        "source": "https://fapi.binance.com/fapi/v1/fundingRate?"
                  f"symbol=FIXTUREUSDT&startTime={declared_start}&endTime={RESERVE_START}&limit=1000",
        "path": "settlements.json",
    }
    write_json(root / entry["path"], [])
    with forbid_io(root), pytest.raises(AccessRefused, match="Source bundle overlaps"):
        read_history_asset(root, entry)


@pytest.mark.parametrize(("start", "end"), [
    (RESERVE_END, RESERVE_END + DAY),
    (HOLDOUT_START, RESERVE_END - 1),
])
def test_t15_m2_config_cannot_unseal_the_registered_reserve(paths, capsys, start, end):
    config_path, manifest_path, root = paths
    config, manifest = map(read_json, paths[:2])
    config["access"]["holdout"] = {"start": start, "end": end}
    config["access"]["approvals"]["development"]["data_window"]["end"] = start
    manifest["assets"].append(
        archive(root, "klines", "1h", "2025-01-01", RESERVE_START,
                RESERVE_START + DAY, candles(RESERVE_START, HOUR, [0])),
    )
    manifest["ingested_at"] = RESERVE_END
    pin(paths, config, manifest)
    with forbid_io(root):
        with pytest.raises(AccessRefused, match="must cover every registered sealed range"):
            load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
        for command in ("fetch", "coverage"):
            assert cli(paths, "development", command) == 1
            assert "must cover every registered sealed range" in capsys.readouterr().err


@pytest.mark.parametrize(("start", "end"), [
    (HOLDOUT_START, HOLDOUT_START + DAY),
    (RESERVE_END + DAY, RESERVE_END + 2 * DAY),
])
def test_t15_m2_config_cannot_omit_a_disjoint_registered_range(paths, capsys, start, end):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    config["access"]["holdout"] = {"start": start, "end": end}
    write_json(config_path, config)
    # The manifest and requested data are otherwise admissible.
    expected = "must cover every registered sealed range"
    with forbid_io(root):
        with pytest.raises(AccessRefused, match=expected):
            load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
        for command in ("fetch", "coverage"):
            assert cli(paths, "development", command) == 1
            assert expected in capsys.readouterr().err


def _seal_record(tmp_path, monkeypatch, *ranges, purpose="m2-retrospective-reserve"):
    record = tmp_path / "seal.json"
    write_json(record, {"version": "history-seal-v1", "ranges": [
        {"purpose": row[2] if len(row) > 2 else purpose, "start": row[0], "end": row[1]}
        for row in ranges
    ]})
    monkeypatch.setattr("acgs.history_seal.RECORD_PATH", record)


@pytest.mark.parametrize("registered_holdout", [False, True])
def test_t15_m2_approves_data_after_a_reserve_but_before_cutoff(
    paths, tmp_path, monkeypatch, registered_holdout,
):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    access = config["access"]
    onboard = access["approvals"]["development"]["data_window"]["start"]
    reserve = (onboard - 3 * DAY, onboard - DAY)
    if registered_holdout:
        prospective = (HOLDOUT_START, HOLDOUT_START + DAY, HOLDOUT_PURPOSE)
        _seal_record(tmp_path, monkeypatch, reserve, prospective)
        access["holdout"] = {"start": reserve[0], "end": prospective[1]}
    else:
        _seal_record(tmp_path, monkeypatch, reserve)
        access["holdout"] = {"start": reserve[0], "end": reserve[1]}
    write_json(config_path, config)
    loaded = load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    assert loaded.market.bars
    assert cli(paths, "development", "fetch") == 0
    assert cli(paths, "development", "coverage") == 0

    access["approvals"]["development"]["data_window"]["start"] = reserve[1] - 1
    write_json(config_path, config)
    with forbid_io(root), pytest.raises(AccessRefused, match="Approved data window overlaps"):
        load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))


@pytest.mark.parametrize("field", ["listing_effective", "schedule_span", "mark_gap_span", "schedule_next"])
def test_t15_later_evidence_cannot_describe_a_sealed_period(paths, capsys, field):
    _, manifest_path, root = paths
    manifest = read_json(manifest_path)
    manifest["version"] = "m1-history-v1"
    manifest.pop("m2_purpose")
    epoch = manifest["listings"][0]["id"]
    later = {"source": "synthetic:backfill", "method": "test", "provenance": "VERIFIED",
             "as_of": RESERVE_END + DAY}
    if field == "listing_effective":
        manifest["listings"].append(manifest["listings"][0] | {
            "status": "DELISTED", "effective": RESERVE_START + 30 * DAY, "evidence": later})
    elif field == "schedule_span":
        manifest["schedules"] = [{"id": epoch, "effective": RESERVE_START - DAY,
                                  "until": RESERVE_START + DAY, "interval_ms": 8 * HOUR,
                                  "evidence": later}]
    elif field == "schedule_next":
        manifest["schedules"] = [{"id": epoch, "effective": RESERVE_START - DAY,
                                  "next_time": RESERVE_START, "evidence": later}]
    else:
        manifest["mark_gaps"] = [{"id": epoch, "start": RESERVE_START - HOUR,
                                  "end": RESERVE_START + HOUR, "evidence": later}]
    write_json(manifest_path, manifest)
    with forbid_io(root):
        with pytest.raises(AccessRefused, match="sealed range"):
            load_history(manifest_path, root)
        assert m1_main(["historical", "--manifest", str(manifest_path), "--assets", str(root)]) == 1
        assert "sealed range" in capsys.readouterr().err


def test_t15_sealed_onboard_identity_stays_admissible(paths):
    # An epoch listed inside a sealed range can trade later. Its onboard instant is an
    # identity anchor, while its inline status evidence starts after the range.
    _, manifest_path, root = paths
    manifest = read_json(manifest_path)
    manifest["version"] = "m1-history-v1"
    manifest.pop("m2_purpose")
    onboard = RESERVE_START + 100 * DAY
    epoch = f"LATEUSDT@{onboard}"
    manifest["instruments"].append({"symbol": "LATEUSDT", "onboard": onboard})
    manifest["listings"].append({
        "id": epoch, "effective": RESERVE_END, "status": "TRADING", "quote": "USDT",
        "contract": "PERPETUAL", "excluded_product": False,
        "evidence": {"source": "synthetic:anchor", "method": "first_observed",
                     "provenance": "RECONSTRUCTED", "as_of": RESERVE_END + DAY,
                     "limitation": "First post-seal observation"},
    })
    write_json(manifest_path, manifest)
    listings = load_history(manifest_path, root).market.listings
    assert any(inst.id == epoch for inst in listings)


def test_t15_frozen_label_cannot_authorize_development(paths, capsys):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    config["protocol_status"] = "FROZEN"
    write_json(config_path, config)
    with forbid_io(root):
        with pytest.raises(AccessRefused, match="frozen authorization is unavailable"):
            load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
        for command in ("fetch", "coverage"):
            assert cli(paths, "development", command) == 1
            assert "frozen authorization is unavailable" in capsys.readouterr().err


def test_coverage_pins_semantics_independently_of_halt_buffer(paths):
    config, manifest, root = paths
    window = Window(START, END)
    policy = _policy(POLICY)
    baseline = coverage(config, manifest, root, "development", window)
    modified = replace(policy, halt_buffer=Decimal("1.00"))
    assert modified.digest != policy.digest
    assert coverage(config, manifest, root, "development", window, modified) == baseline
    for variant in (
        replace(policy, funding_ownership_window_ms=0),
        replace(policy, funding_jitter_ms=policy.funding_jitter_ms + 1),
        replace(policy, funding_intervals_ms=(HOUR, 2 * HOUR)),
        replace(policy, decimal_precision=policy.decimal_precision + 1),
        replace(policy, accounting_tolerance=Decimal("0.00000002")),
    ):
        with forbid_io(root), pytest.raises(AccessRefused, match="pinned M1 semantics"):
            coverage(config, manifest, root, "development", window, variant)


def test_m2_requires_announcement_source_for_maintenance_tiers(paths):
    config_path, manifest_path, root = paths
    config, manifest = map(read_json, paths[:2])
    evidence = {"source": "https://fapi.binance.com/fapi/v1/leverageBracket",
                "method": "synthetic", "provenance": "VERIFIED", "as_of": START}
    manifest["maintenance"] = [{
        "id": manifest["listings"][0]["id"], "effective": START, "evidence": evidence,
        "tiers": [{"ceiling": "1000", "rate": "0.01", "deduction": "0", "max_leverage": 5}],
    }]
    pin(paths, config, manifest)
    with forbid_io(root), pytest.raises(AccessRefused, match="dated Binance announcement"):
        load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    evidence["source"] = "https://www.binance.com/en/support/announcement/detail/synthetic-notice"
    pin(paths, config, manifest)
    with forbid_io(root / "unexpected"):
        loaded = load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    assert next(iter(loaded.market.maintenance.values()))[0].evidence.as_of == START


def test_t15_capture_gate_reads_only_allowed_blobs_by_receipt(tmp_path, monkeypatch):
    db = tmp_path / "capture.sqlite3"
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    allowed = b'{"symbols":[]}'
    good_sha = hashlib.sha256(allowed).hexdigest()
    sealed_sha = "0" * 64
    sealed_receipt = "2025-12-31T23:59:59.999999+00:00"
    allowed_receipt = "2026-01-01T00:00:00+00:00"
    with open_ledger(db) as conn, conn:
        conn.executemany(
            """INSERT INTO capture_attempts
               (source_url, slot, requested_at, received_at, status, http_status,
                payload, sha256, provenance, payload_encoding)
               VALUES (?, ?, ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED', ?)""",
            [
                (url, "seal", sealed_receipt, sealed_receipt, b"invalid zlib", sealed_sha, "zlib"),
                (url, "safe", allowed_receipt, allowed_receipt, allowed, good_sha, None),
            ],
        )
    manifest_path = tmp_path / "capture.json"
    manifest = {
        "version": "m1-history-v1", "ingested_at": RESERVE_END, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000, "instruments": [], "listings": [], "assets": [],
        "capture": {"cutoff": RESERVE_END, "symbols": [],
                    "digest": digest([digest((url, allowed_receipt, good_sha))])},
    }
    write_json(manifest_path, manifest)
    queries = []
    original_connect = sqlite3.connect

    def traced(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr("acgs.history_io.sqlite3.connect", traced)
    assert load_history(manifest_path, tmp_path, db).digest == digest(manifest)
    blob_queries = [query for query in queries if "SELECT payload" in query]
    assert len(blob_queries) == 1 and blob_queries[0].endswith("WHERE id = 2")
    queries.clear()
    manifest["capture"]["digest"] = digest([
        digest((url, sealed_receipt, sealed_sha)), digest((url, allowed_receipt, good_sha)),
    ])
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Capture selection differs"):
        load_history(manifest_path, tmp_path, db)
    assert not any("SELECT payload" in query for query in queries)
    manifest["capture"]["cutoff"] = RESERVE_END - 1
    write_json(manifest_path, manifest)
    queries.clear()
    with pytest.raises(AccessRefused, match="Capture cutoff overlaps"):
        load_history(manifest_path, tmp_path, db)
    assert queries == []


@pytest.mark.parametrize("case", ["cutoff_after_registered", "data_after_registered", "truncated_reserve"])
def test_t15_config_cannot_shift_research_chronology(paths, tmp_path, monkeypatch, capsys, case):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    approval = config["access"]["approvals"]["development"]
    if case == "truncated_reserve":
        # The registered 2025 reserve, cut short by the config.
        config["access"]["holdout"] = {"start": RESERVE_START, "end": RESERVE_START + 150 * DAY}
        approval["data_window"]["end"] = HOLDOUT_START
        expected = "must cover every registered sealed range"
    else:
        # An independent prospective holdout one day long.
        registered = (HOLDOUT_START, HOLDOUT_START + DAY, HOLDOUT_PURPOSE)
        _seal_record(tmp_path, monkeypatch, registered)
        later = HOLDOUT_START + 10 * DAY
        config["access"]["holdout"] = {"start": registered[0], "end": registered[1]}
        if case == "cutoff_after_registered":
            config["access"]["research_cutoff"] = later
            expected = "Research cutoff falls after a registered holdout starts"
        else:
            approval["data_window"] = {"start": HOLDOUT_START + 2 * DAY, "end": later}
            approval["decision_window"] = {"start": HOLDOUT_START + 3 * DAY,
                                           "end": HOLDOUT_START + 4 * DAY}
            expected = "Approved data window crosses the research cutoff"
    write_json(config_path, config)
    with forbid_io(root):
        with pytest.raises(AccessRefused, match=expected):
            load_research_inputs(config_path, manifest_path, root, "development", None)
        assert cli(paths, "development", "fetch") == 1
        assert expected in capsys.readouterr().err


def test_t15_holdout_registration_keeps_prior_development_valid(paths, tmp_path, monkeypatch):
    config_path, manifest_path, root = paths
    _seal_record(tmp_path, monkeypatch,
                 (HOLDOUT_START, HOLDOUT_START + DAY, HOLDOUT_PURPOSE),
                 (HOLDOUT_START + 20 * DAY, HOLDOUT_START + 30 * DAY))
    config = read_json(config_path)
    config["access"]["holdout"] = {"start": HOLDOUT_START, "end": HOLDOUT_START + 30 * DAY}
    write_json(config_path, config)
    assert load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    record = read_json(tmp_path / "seal.json")
    record["ranges"][0]["purpose"] = "holdout"
    write_json(tmp_path / "seal.json", record)
    with pytest.raises(AccessRefused, match="known purpose"):
        load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))


def _boundary_capture(tmp_path, receipt, next_time):
    db = tmp_path / "boundary.sqlite3"
    exchange = json.dumps({"symbols": [{
        "symbol": "BTCUSDT", "onboardDate": 1569398400000, "status": "TRADING",
        "quoteAsset": "USDT", "contractType": "PERPETUAL", "filters": [],
    }]}).encode()
    premium = json.dumps([{"symbol": "BTCUSDT", "nextFundingTime": next_time}]).encode()
    rows = [
        ("https://fapi.binance.com/fapi/v1/exchangeInfo", exchange),
        ("https://fapi.binance.com/fapi/v1/premiumIndex", premium),
    ]
    with open_ledger(db) as conn, conn:
        conn.executemany(
            """INSERT INTO capture_attempts
               (source_url, slot, requested_at, received_at, status, http_status,
                payload, sha256, provenance, payload_encoding)
               VALUES (?, ?, ?, ?, 'CAPTURED', 200, ?, ?, 'VERIFIED', NULL)""",
            [(url, url, receipt, receipt, body, hashlib.sha256(body).hexdigest())
             for url, body in rows],
        )
    hashes = [digest((url, receipt, hashlib.sha256(body).hexdigest())) for url, body in rows]
    return db, digest(hashes)


@pytest.mark.parametrize("path", ["inline", "capture"])
def test_t15_pre_seal_forecast_is_admissible_on_both_input_paths(tmp_path, path):
    # A premiumIndex forecast received before the reserve may name its first instant.
    receipt_ms = RESERVE_START - 60_000
    receipt = "2024-12-31T23:59:00+00:00"
    epoch = "BTCUSDT@1569398400000"
    manifest = {
        "version": "m1-history-v1", "ingested_at": RESERVE_END, "candle_delay_ms": 1000,
        "funding_delay_ms": 5000, "instruments": [{"symbol": "BTCUSDT", "onboard": 1569398400000}],
        "listings": [], "assets": [],
    }
    db = None
    if path == "inline":
        manifest["listings"] = [{
            "id": epoch, "effective": 1569398400000, "status": "TRADING", "quote": "USDT",
            "contract": "PERPETUAL", "excluded_product": False,
            "evidence": {"source": "https://fapi.binance.com/fapi/v1/exchangeInfo",
                         "method": "m0_capture", "provenance": "VERIFIED", "as_of": receipt_ms},
        }]
        manifest["schedules"] = [{
            "id": epoch, "effective": receipt_ms, "next_time": RESERVE_START,
            "evidence": {"source": "https://fapi.binance.com/fapi/v1/premiumIndex",
                         "method": "m0_capture", "provenance": "VERIFIED", "as_of": receipt_ms},
        }]
    else:
        db, selection = _boundary_capture(tmp_path, receipt, RESERVE_START)
        manifest["capture"] = {"cutoff": RESERVE_START - 1, "symbols": ["BTCUSDT"],
                               "digest": selection}
    manifest_path = tmp_path / "boundary.json"
    write_json(manifest_path, manifest)
    market = load_history(manifest_path, tmp_path, db).market
    schedules = [row for rows in market.schedules.values() for row in rows]
    assert [(row.effective, row.next_time) for row in schedules if row.next_time] == [
        (receipt_ms, RESERVE_START),
    ]

    # The same forecast recorded after the reserve began describes the sealed period.
    if path == "inline":
        manifest["schedules"][0]["evidence"]["as_of"] = RESERVE_END
        write_json(manifest_path, manifest)
        with pytest.raises(AccessRefused, match="describes a registered sealed range"):
            load_history(manifest_path, tmp_path)
