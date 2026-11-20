import base64
import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evidence/m2/extract_trading_rules.py"
spec = spec_from_file_location("extract_trading_rules", SCRIPT)
assert spec is not None and spec.loader is not None
extract = module_from_spec(spec)
spec.loader.exec_module(extract)

TIMESTAMP = "20240301072349"
APP_START = '<script id="__APP_DATA" type="application/json">'
PRODUCT = {
    "symbol": "EXAMPLEUSDT", "quoteAsset": "USDT", "contractType": "PERPETUAL",
    "onboardDate": 1660000000123, "status": "TRADING", "filters": [{"value": "1.00"}],
}


def write_copy(root: Path, html: str) -> Any:
    raw = html.encode("utf-8")
    (root / (TIMESTAMP + ".bin")).write_bytes(raw)
    return extract.RulePin(
        TIMESTAMP, "https://www.binance.com/en/futures/trading-rules/", hashlib.sha256(raw).hexdigest(),
        base64.b32encode(hashlib.sha1(raw).digest()).decode(),
    )


def test_extract_names_and_dates_from_complete_archived_json(tmp_path: Path) -> None:
    products = [PRODUCT, {**PRODUCT, "symbol": "EXAMPLEUSDC", "quoteAsset": "USDC"},
                {**PRODUCT, "symbol": "FUTUREUSDT", "contractType": "CURRENT_QUARTER"}]
    state = {"pageData": {"redux": {"products": {"usdtFuturesProducts": products}}}}
    pin = write_copy(tmp_path, APP_START + json.dumps(state) + "</script>")
    result = extract.extract_capture(tmp_path, pin)
    assert result["as_of"] == "2024-03-01T07:23:49Z"
    assert result["complete_product_rows"] == 3
    assert result["embedded_product_array_complete"]
    assert result["usdt_perpetual_rows"] == [{"symbol": "EXAMPLEUSDT", "onboard_at": "2022-08-08T23:06:40.123Z"}]


def test_truncated_array_retains_only_complete_product_rows(tmp_path: Path) -> None:
    prefix = '{"pageData":{"redux":{"products":{"loading":false,"usdtFuturesProducts":['
    pin = write_copy(tmp_path, APP_START + prefix + json.dumps(PRODUCT) + ',{"symbol":"INCOMPLETEUSDT"')
    result = extract.extract_capture(tmp_path, pin)
    assert result["complete_product_rows"] == 1
    assert not result["embedded_product_array_complete"]
    assert [row["symbol"] for row in result["usdt_perpetual_rows"]] == ["EXAMPLEUSDT"]


def test_capture_cutoff_is_preflighted_before_open(tmp_path: Path) -> None:
    pin = extract.PINS[0]._replace(timestamp="20250101000000")
    with pytest.raises(ValueError, match="outside the O2 cutoff"):
        extract.extract_capture(tmp_path, pin)


def test_product_onboard_date_cannot_follow_the_capture(tmp_path: Path) -> None:
    products = [{**PRODUCT, "onboardDate": 1735689600000}]
    state = {"pageData": {"redux": {"products": {"usdtFuturesProducts": products}}}}
    pin = write_copy(tmp_path, APP_START + json.dumps(state) + "</script>")
    with pytest.raises(ValueError, match="onboardDate is later"):
        extract.extract_capture(tmp_path, pin)


def test_archived_html_requires_pinned_hash(tmp_path: Path) -> None:
    pin = write_copy(tmp_path, "synthetic other bytes")._replace(raw_sha256="0" * 64)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        extract.extract_capture(tmp_path, pin)
