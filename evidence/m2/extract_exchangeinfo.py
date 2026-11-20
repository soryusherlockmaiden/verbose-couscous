"""Extract the two pinned Wayback exchangeInfo copies for O2 Stage A offline."""

import argparse
import base64
import gzip
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

CUTOFF = datetime(2025, 1, 1, tzinfo=UTC)
RAW_ROOT = Path("data/o2-inventory/snapshots-wayback")
EXCHANGE_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
PROVENANCE = "third-party archive copy of exchange JSON"
FILTER_FIELDS = {
    "PRICE_FILTER": {"minPrice", "maxPrice", "tickSize"},
    "LOT_SIZE": {"minQty", "maxQty", "stepSize"},
    "MARKET_LOT_SIZE": {"minQty", "maxQty", "stepSize"},
    "MIN_NOTIONAL": {"notional"},
    "PERCENT_PRICE": {"multiplierDown", "multiplierUp", "multiplierDecimal"},
}


class SnapshotPin(NamedTuple):
    timestamp: str
    raw_sha256: str
    cdx_digest: str
    original_http_date: str
    retrieved_at: str


PINS = (
    SnapshotPin(
        "20221127174756",
        "40157f8c9091102c8b2107abd2908909428f96f6a78a1bd5c38e42bb53bd5f92",
        "BYF5QUH3SZRUHUZ6R2KZEUG5XNZ4XW2G",
        "2022-11-27T17:47:57Z",
        "2026-11-19T15:47:13Z",
    ),
    SnapshotPin(
        "20231102093209",
        "594f7ec2a1c1c4ea8b115ff9c049fc9cb91c2db20322b8d4af75dd1cc32c1cb3",
        "7RTHSJVGSWZYNUP2ETU4ZTPTAMBXRFWM",
        "2023-11-02T09:32:09Z",
        "2026-11-19T15:47:15Z",
    ),
)


def utc_millis(value: int) -> str:
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def extract_symbol(row: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, dict[str, Any]] = {}
    for rule in row["filters"]:
        name = rule["filterType"]
        if name not in FILTER_FIELDS:
            continue
        if name in filters or not FILTER_FIELDS[name].issubset(rule):
            raise ValueError(f"Duplicate or incomplete {name} for {row['symbol']}")
        filters[name] = {field: rule[field] for field in sorted(FILTER_FIELDS[name])}
    if filters.keys() != FILTER_FIELDS.keys():
        raise ValueError(f"Missing filter families for {row['symbol']}")
    return {
        "symbol": row["symbol"],
        "status": row["status"],
        "onboard_at": utc_millis(row["onboardDate"]),
        "filters": dict(sorted(filters.items())),
    }


def extract_snapshot(root: Path, pin: SnapshotPin) -> dict[str, Any]:
    capture = datetime.strptime(pin.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if capture >= CUTOFF:
        raise ValueError("Capture is outside the O2 cutoff")
    as_of = capture.isoformat().replace("+00:00", "Z")
    filename = f"binance-usdm-exchangeinfo-{pin.timestamp}.json.gz"
    raw = (root / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin.raw_sha256:
        raise ValueError(f"Raw SHA-256 mismatch for {filename}")
    if base64.b32encode(hashlib.sha1(raw).digest()).decode("ascii") != pin.cdx_digest:
        raise ValueError(f"Wayback CDX digest mismatch for {filename}")
    decoded = gzip.decompress(raw)
    exchange = json.loads(decoded)
    symbols = sorted((extract_symbol(row) for row in exchange["symbols"]
                      if row["quoteAsset"] == "USDT" and row["contractType"] == "PERPETUAL"),
                     key=lambda row: row["symbol"])
    if not symbols or len(symbols) != len({row["symbol"] for row in symbols}):
        raise ValueError(f"Empty or duplicate USDT perpetual symbols in {filename}")
    return {
        "source_id": f"WAYBACK_{pin.timestamp}",
        "capture_url": f"https://web.archive.org/web/{pin.timestamp}id_/{EXCHANGE_URL}",
        "as_of": as_of,
        "original_http_date": pin.original_http_date,
        "server_time": utc_millis(exchange["serverTime"]),
        "retrieved_at": pin.retrieved_at,
        "raw_path": (RAW_ROOT / filename).as_posix(),
        "raw_size_bytes": len(raw),
        "raw_sha256": pin.raw_sha256,
        "cdx_digest": pin.cdx_digest,
        "decoded_json_sha256": hashlib.sha256(decoded).hexdigest(),
        "exchange_rows": len(exchange["symbols"]),
        "usdt_perpetual_status_counts": dict(sorted(Counter(row["status"] for row in symbols).items())),
        "symbols": symbols,
    }


def render(snapshots: list[dict[str, Any]]) -> str:
    header = {
        "version": "m2-o2-wayback-exchangeinfo-v1",
        "cutoff_exclusive": CUTOFF.isoformat().replace("+00:00", "Z"),
        "original_url": EXCHANGE_URL,
        "provenance": PROVENANCE,
        "observed_contract_type": "PERPETUAL",
        "observed_quote_asset": "USDT",
        "observed_at_rule": "Capture time is the as-of. Exchange HTTP Date and serverTime are retained separately.",
        "use_limit": "Point observations for O2 and O3 review. Use outside each as-of needs an O3 validity rule.",
    }
    blocks = []
    for snapshot in snapshots:
        meta = {key: val for key, val in snapshot.items() if key != "symbols"}
        meta_text = json.dumps(meta, indent=2)[:-2].replace("\n", "\n    ")
        rows = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in snapshot["symbols"]]
        blocks.append("    " + meta_text + ',\n      "symbols": [\n        '
                      + ",\n        ".join(rows) + "\n      ]\n    }")
    return json.dumps(header, indent=2)[:-2] + ',\n  "snapshots": [\n' + ",\n".join(blocks) + "\n  ]\n}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshots = [extract_snapshot(args.raw_dir, pin) for pin in PINS]
    args.output.write_text(render(snapshots), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
