"""Generate a deterministic synthetic Phase A manifest and archive tree."""

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from acgs.m2_data import ARCHIVE_HOST, INTERVALS, utc_ms
from acgs.pit import HOUR
from acgs.sim_config import canonical, digest

SYMBOL = "FIXTUREUSDT"
DAY_START = utc_ms("2000-01-01T00:00:00Z")
HOLDOUT_START = utc_ms("2000-01-04T00:00:00Z")
ONBOARD = utc_ms("1999-12-28T00:00:00Z")
EPOCH = f"{SYMBOL}@{ONBOARD}"
START = DAY_START
END = START + 2 * HOUR


def write_json(path, value):
    path.write_bytes((canonical(value) + "\n").encode("utf-8"))


def archive(root, kind, interval, label, start, end, rows, *, period="daily"):
    name = f"{SYMBOL}-{interval}" if interval else SYMBOL
    directory = f"{SYMBOL}/{interval}" if interval else SYMBOL
    path = f"data/futures/um/{period}/{kind}/{directory}/{name}-{label}.zip"
    payload = BytesIO()
    with ZipFile(payload, "w") as handle:
        member = ZipInfo(f"{name}-{label}.csv")
        member.create_system = 0
        handle.writestr(member, "".join(",".join(map(str, row)) + "\n" for row in rows))
    raw = payload.getvalue()
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {
        "kind": kind,
        "id": EPOCH,
        "start": start,
        "end": end,
        "source": f"{ARCHIVE_HOST}/{path}",
        "path": path,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    } | ({"interval_ms": INTERVALS[interval]} if interval else {})


def candles(start, size, indices):
    return [
        [start + index * size, 100, 101, 99, 100, 2, start + (index + 1) * size - 1,
         200, 2, 0, 0, 0]
        for index in indices
    ]


def make_fixture(output):
    root = output / "assets"
    assets = [
        archive(
            root, kind, interval, "2000-01-01", DAY_START, DAY_START + INTERVALS["1d"],
            candles(DAY_START, INTERVALS[interval], range(1) if interval != "15m" else [0, 1, 3]),
        )
        for kind in ("klines", "markPriceKlines")
        for interval in INTERVALS
    ]
    assets.append(archive(
        root, "fundingRate", None, "2000-01-01", DAY_START, DAY_START + INTERVALS["1d"],
        [[DAY_START + 10, 1, "0.001"], [DAY_START + HOUR + 60_000, 1, "0.001"]],
    ))
    manifest = {
        "version": "m2-history-v1",
        "m2_purpose": "development",
        "ingested_at": "2000-02-01T00:00:00Z",
        "candle_delay_ms": 1000,
        "funding_delay_ms": 5000,
        "instruments": [{"symbol": SYMBOL, "onboard": ONBOARD}],
        "listings": [{
            "id": EPOCH,
            "effective": ONBOARD,
            "status": "TRADING",
            "quote": "USDT",
            "contract": "PERPETUAL",
            "excluded_product": False,
            "evidence": {
                "source": "synthetic:fixture",
                "method": "synthetic",
                "provenance": "RECONSTRUCTED",
                "as_of": ONBOARD,
                "limitation": "Fixture epoch anchor",
            },
        }],
        "assets": assets,
    }
    config = {
        "version": "m2-v1",
        "mode": "OFFLINE",
        "protocol_status": "DRAFT",
        "m1": {
            "replay": "m1-replay-v8",
            "policy": "m1-v3",
            "policy_sha256": "dd0187eb938370e1cb72b6a02d08752da185549fbfb99741bb71acddeec4b76e",
        },
        "access": {
            "holdout": {"start": HOLDOUT_START, "end": "2000-02-01T00:00:00Z"},
            "approvals": {"development": {
                "manifest_sha256": digest(manifest),
                "decision_window": {"start": START, "end": END},
                "data_window": {"start": ONBOARD, "end": HOLDOUT_START},
                "signal_warmup_bars": 2,
                "history_days": 1,
                "funding_lookback_ms": 2 * HOUR,
                "funding_lookahead_ms": 8 * HOUR,
            }},
        },
    }
    write_json(output / "m2.json", config)
    write_json(output / "manifest.json", manifest)
    return config, manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    make_fixture(parser.parse_args().output)
