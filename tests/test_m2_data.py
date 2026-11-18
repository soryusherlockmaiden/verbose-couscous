import hashlib
import json
from datetime import timedelta
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

from acgs.history_io import read_json
from acgs.m2_data import AccessRefused, Window, fetch_inputs, load_research_inputs
from acgs.m2_io import main
from acgs.market_data import PublicResponse
from acgs.pit import HOUR
from acgs.sim_config import EPOCH, digest

POLICY = Path(__file__).resolve().parents[1] / "config" / "m1.json"


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


def forbid_payload_reads(monkeypatch, root):
    original_open = Path.open
    reads = []

    def watch(path, *args, **kwargs):
        if path.is_relative_to(root):
            reads.append(path)
            pytest.fail(f"Attempted payload I/O at {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watch)
    return reads


@pytest.mark.parametrize("case", ["holdout_role", "direct", "dependency", "funding_dependency"])
def test_t15_seal_refuses_research_and_coverage_before_payload_read(paths, monkeypatch, capsys, case):
    config_path, manifest_path, root = paths
    config = read_json(config_path)
    manifest = read_json(manifest_path)
    held_out = archive(
        root, "klines", "1h", "2000-01-04", HOLDOUT_START, HOLDOUT_START + 24 * HOUR,
        candles(HOLDOUT_START, HOUR, [0]),
    )
    manifest["assets"].append(held_out)
    config["access"]["approvals"]["development"]["decision_window"]["end"] = HOLDOUT_START
    config["protocol_status"] = "FROZEN" if case == "holdout_role" else "DRAFT"
    pin(paths, config, manifest)
    reads = forbid_payload_reads(monkeypatch, root)
    purpose = "holdout" if case == "holdout_role" else "development"
    start = HOLDOUT_START - HOUR if case == "dependency" else HOLDOUT_START
    end = HOLDOUT_START if case == "dependency" else HOLDOUT_START + HOUR
    expected = "dependency exceeds" if case == "dependency" else "holdout seal"
    if case == "holdout_role":
        expected = "Holdout is sealed"
    if case == "funding_dependency":
        start, end = HOLDOUT_START - 3 * HOUR, HOLDOUT_START - 2 * HOUR
        expected = "fundingRate dependency exceeds"
    with pytest.raises(AccessRefused, match=expected):
        load_research_inputs(config_path, manifest_path, root, purpose, Window(start, end))
    assert cli(paths, purpose, "coverage", start=start, end=end) == 1
    assert expected in capsys.readouterr().err
    assert reads == []


@pytest.mark.parametrize("case", [
    "bad_digest", "bad_pin", "overlap_klines", "overlap_markPriceKlines",
    "overlap_fundingRate", "wrong_interval", "alias",
])
def test_t15_fetch_preflights_entire_manifest_and_load_reads_nothing(
    paths, monkeypatch, capsys, case,
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
    reads = forbid_payload_reads(monkeypatch, root)
    requests = []

    def unexpected(url, *, max_payload):
        requests.append(url)
        pytest.fail(f"Made a request for {url}")

    monkeypatch.setattr("acgs.m2_data.fetch_public", unexpected)
    assert cli(paths, "development", "fetch") == 1
    assert expected in capsys.readouterr().err
    with pytest.raises(AccessRefused, match=expected):
        load_research_inputs(config_path, manifest_path, root, "development", Window(START, END))
    assert reads == requests == []


def test_draft_without_approvals_refuses_fetch_and_coverage(paths, monkeypatch, capsys):
    config_path, _, root = paths
    config = read_json(config_path)
    config["access"] = {"holdout": None, "approvals": {}}
    write_json(config_path, config)
    reads = forbid_payload_reads(monkeypatch, root)
    assert cli(paths, "development", "fetch") == 1
    assert cli(paths, "development", "coverage") == 1
    assert "dataset approval is OPEN" in capsys.readouterr().err
    assert reads == []


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
