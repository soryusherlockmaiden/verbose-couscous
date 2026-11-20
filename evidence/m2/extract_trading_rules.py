"""Extract discovery-only names and dates from pinned archived trading-rules HTML."""

import argparse
import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

CUTOFF = datetime(2025, 1, 1, tzinfo=UTC)
RAW_ROOT = Path("data/o2-inventory/a1-wayback-review")
APP_DATA = re.compile(r'<script\b[^>]*\sid\s*=\s*"__APP_DATA"[^>]*>')
PRODUCTS = re.compile(r'"products"\s*:\s*\{\s*"loading"\s*:\s*false\s*,\s*"usdtFuturesProducts"\s*:\s*\[')


class RulePin(NamedTuple):
    timestamp: str
    original_url: str
    raw_sha256: str
    cdx_digest: str


PINS = (
    RulePin(
        "20240301072349", "https://www.binance.com/en/futures/trading-rules/",
        "88fb7c425fd7e2f9cd981674fffa16eebab94955e269a8f43966312f28edfca3",
        "6UCZ2NGLJHHKYHSB75UXYRQQ3ALOFTQT",
    ),
    RulePin(
        "20240715172835", "https://www.binance.com/en/futures/trading-rules",
        "9997c73e4a555ed51f9a2d09d39de04b02b53eb3383e9a3389758048e07eed1a",
        "HMQ4FPTJ4YAYAR7F3XY73FERP2DHC5KB",
    ),
)


def partial_product_array(state: str) -> tuple[list[dict[str, Any]], bool]:
    markers = list(PRODUCTS.finditer(state))
    if len(markers) != 1:
        raise ValueError("Expected a unique embedded USD-M product array")
    remaining = state[markers[0].end():].lstrip()
    decoder = json.JSONDecoder()
    rows: list[dict[str, Any]] = []
    if remaining.startswith("]"):
        return rows, True
    while remaining:
        try:
            row, end = decoder.raw_decode(remaining)
        except json.JSONDecodeError:
            break
        remaining = remaining[end:].lstrip()
        if not remaining.startswith((",", "]")):
            break
        if not isinstance(row, dict):
            raise ValueError("Expected product object in archived array")
        rows.append(row)
        if remaining.startswith("]"):
            return rows, True
        remaining = remaining[1:].lstrip()
    return rows, False


def embedded_products(html: str) -> tuple[list[dict[str, Any]], bool]:
    scripts = list(APP_DATA.finditer(html))
    if len(scripts) != 1:
        raise ValueError("Expected one archived APP_DATA script")
    state = html[scripts[0].end():]
    full, end, _ = state.partition("</script>")
    if not end:
        return partial_product_array(state)
    products: list[dict[str, Any]] = json.loads(full)["pageData"]["redux"]["products"]["usdtFuturesProducts"]
    return products, True


def extract_capture(root: Path, pin: RulePin) -> dict[str, Any]:
    captured = datetime.strptime(pin.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if captured >= CUTOFF:
        raise ValueError("Capture is outside the O2 cutoff")
    name = pin.timestamp + ".bin"
    raw = (root / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin.raw_sha256:
        raise ValueError(f"Raw SHA-256 mismatch for {name}")
    if base64.b32encode(hashlib.sha1(raw).digest()).decode("ascii") != pin.cdx_digest:
        raise ValueError(f"Wayback CDX digest mismatch for {name}")
    html = raw.decode("utf-8")
    products, complete = embedded_products(html)
    observed = []
    for row in products:
        if row["quoteAsset"] != "USDT" or row["contractType"] != "PERPETUAL":
            continue
        onboard = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=row["onboardDate"])
        if onboard > captured:
            raise ValueError("Embedded onboardDate is later than the permitted observation")
        observed.append({"symbol": row["symbol"], "onboard_at": onboard.isoformat(timespec="milliseconds").replace("+00:00", "Z")})
    if not observed or len(observed) != len({row["symbol"] for row in observed}):
        raise ValueError("Empty or duplicate archived USDT perpetual products")
    return {
        "source_id": f"WAYBACK_RULES_{pin.timestamp}",
        "capture_url": f"https://web.archive.org/web/{pin.timestamp}id_/{pin.original_url}",
        "as_of": captured.isoformat().replace("+00:00", "Z"),
        "raw_path": (RAW_ROOT / name).as_posix(),
        "raw_size_bytes": len(raw),
        "raw_sha256": pin.raw_sha256,
        "cdx_digest": pin.cdx_digest,
        "embedded_product_array_complete": complete,
        "complete_product_rows": len(products),
        "usdt_perpetual_rows": sorted(observed, key=lambda row: row["symbol"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {
        "version": "m2-o2-trading-rules-snapshots-v1",
        "cutoff_exclusive": CUTOFF.isoformat().replace("+00:00", "Z"),
        "provenance": "third-party archive copy of exchange trading-rules HTML",
        "use_limit": "Discovery names and reported onboard dates. An archived product row carries no approved trading status or filter validity.",
        "snapshots": [extract_capture(args.raw_dir, pin) for pin in PINS],
    }
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
