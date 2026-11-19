import base64
import gzip
import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evidence/m2/extract_exchangeinfo.py"
spec = spec_from_file_location("extract_exchangeinfo", SCRIPT)
assert spec is not None and spec.loader is not None
extract = module_from_spec(spec)
spec.loader.exec_module(extract)


def test_archive_metadata_and_decimal_filters_are_reproduced(tmp_path: Path) -> None:
    symbol = {
        "symbol": "EXAMPLEUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL",
        "status": "SETTLING", "onboardDate": 1660000000123,
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.00100", "maxPrice": "1000", "tickSize": "0.00100"},
            {"filterType": "LOT_SIZE", "minQty": "0.100", "maxQty": "10000", "stepSize": "0.100"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "1", "maxQty": "100", "stepSize": "1"},
            {"filterType": "MIN_NOTIONAL", "notional": "7.50"},
            {"filterType": "PERCENT_PRICE", "multiplierUp": "1.05", "multiplierDown": "0.95", "multiplierDecimal": "4"},
        ],
    }
    raw = gzip.compress(json.dumps({"serverTime": 1660000011000, "symbols": [
        symbol, {**symbol, "symbol": "OTHERUSDC", "quoteAsset": "USDC"},
    ]}).encode(), mtime=0)
    pin = extract.SnapshotPin(
        "20221127174756", hashlib.sha256(raw).hexdigest(), base64.b32encode(hashlib.sha1(raw).digest()).decode(),
        "2022-11-27T17:47:57Z", "2026-11-19T15:47:13Z",
    )
    (tmp_path / f"binance-usdm-exchangeinfo-{pin.timestamp}.json.gz").write_bytes(raw)
    snapshot = extract.extract_snapshot(tmp_path, pin)
    expected = json.loads(extract.render([snapshot]))
    assert expected["snapshots"][0] == snapshot
    assert snapshot["as_of"] == "2022-11-27T17:47:56Z"
    assert snapshot["server_time"] == "2022-08-08T23:06:51.000Z"
    assert snapshot["usdt_perpetual_status_counts"] == {"SETTLING": 1}
    assert snapshot["symbols"][0]["filters"]["PRICE_FILTER"]["tickSize"] == "0.00100"
    assert snapshot["symbols"][0]["filters"]["LOT_SIZE"]["stepSize"] == "0.100"
    assert snapshot["symbols"][0]["filters"]["MIN_NOTIONAL"]["notional"] == "7.50"
    assert snapshot["raw_sha256"] == pin.raw_sha256 and snapshot["cdx_digest"] == pin.cdx_digest


def test_cutoff_is_checked_before_reading_archive_bytes(tmp_path: Path) -> None:
    pin = extract.PINS[0]._replace(timestamp="20250101000000")
    with pytest.raises(ValueError, match="outside the O2 cutoff"):
        extract.extract_snapshot(tmp_path, pin)


@pytest.mark.parametrize("validate_sha256", [True, False])
def test_wrong_archive_digest_is_refused_before_decompression(tmp_path: Path, validate_sha256: bool) -> None:
    raw = b"synthetic non-gzip replacement"
    pin = extract.PINS[0]
    if not validate_sha256:
        pin = pin._replace(raw_sha256=hashlib.sha256(raw).hexdigest())
    (tmp_path / f"binance-usdm-exchangeinfo-{pin.timestamp}.json.gz").write_bytes(raw)
    with pytest.raises(ValueError, match="SHA-256 mismatch" if validate_sha256 else "CDX digest mismatch"):
        extract.extract_snapshot(tmp_path, pin)
