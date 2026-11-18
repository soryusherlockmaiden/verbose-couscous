# 50 USDT Execution Feasibility Report

Report schema `m1-feasibility-v2` (v2 adds the inconclusive status after the independent review; v1 output differed only in `version` and `status`). This is an execution feasibility sample for M1. The sample is development evidence and no M2 holdout was used.

## Reproduce

```text
uv sync --locked
uv run --locked python evidence/m1/fetch.py evidence/m1/binance-2024.json data/m1/archive
uv run --locked python -m acgs historical --manifest evidence/m1/binance-2024.json --assets data/m1/archive
uv run --locked python -m acgs feasibility --manifest evidence/m1/binance-2024.json --assets data/m1/archive --policy config/m1.json --start 2024-03-01T00:00:00Z --end 2024-04-11T00:00:00Z --out data/m1/feasibility-2024-v2.json
```

| Identifier | SHA-256 digest |
|---|---|
| [Dataset manifest](binance-2024.json) | `44d93cb13430d499dd3f8a4e73cb6fd1b4539357705a613fbb6ee90c26b02aa5` |
| [OFFLINE policy](../../config/m1.json) | `307b338aa93de48205104167c161e007f6604c5fb0468d263b4fa886703cb836` |
| Canonical feasibility output, excluding final LF | `09d90ebcb1836805fb4b2d4386ba7da8e8aedbb40fcebd30ebcd5111556ea69c` |

The manifest and policy values are the `dataset_digest` and `policy_digest`: SHA-256 of their canonical JSON, not of the file bytes. The v1 output hash was `cc6ac3f0584ffe480771a9873704cfe3ed168f0e35b80a974496c010e60a430d`.

## Coverage and provenance

| USD-M epoch in this sample | Contract 1d bars | Matching mark 1d bars | Settlement records | PIT filters | PIT maintenance |
|---|---:|---:|---:|---:|---|
| BTCUSDT | 121 | 121 | 363 | 0 | 0 |
| ETHUSDT | 121 | 121 | 363 | 0 | 0 |
| DGBUSDT | 91 | 91 | 273 | 0 | Smallest announced bracket from 2024-03-25 10:30 UTC, subject to snapshot age |

- The 33 archive ZIPs matched Binance's published SHA-256 files. The manifest pins each URL, hash, and coverage window. BTCUSDT and ETHUSDT cover January through April 2024. The DGBUSDT sample ends after March, before the delisting settlement. The first observed day is a reconstructed sample anchor for each epoch. The sample does not establish exchange-wide historical membership. [Binance Public Data](https://github.com/binance/binance-public-data).
- A direct public `markPriceKlines` query for BTCUSDT returned HTTP 200 for the first two daily bars of March 2024. The opening and closing values matched the archive. The research sample needed no mark proxy. Daily OHLC coverage supplies an envelope and cannot establish tick chronology or coverage at 15m/1h. [Binance market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
- DGBUSDT close-only at 2024-04-01 08:30 UTC and delisting at 09:00 UTC come from an announcement published on March 25. Boundary tests use fixtures for distinct relisting epochs. The March 25 announcement also supplies a dated first maintenance bracket. No historical brackets were found for the sampled BTC/ETH windows. Binance's bracket endpoint requires account access. [Delisting and tier announcement](https://www.binance.com/en/support/announcement/detail/ce74360a3fd444b2b35da7186f8dd0ad) · [Binance account reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account).
- Binance `exchangeInfo` exposes current symbol rules. The M0 database yielded contemporaneous BTCUSDT/ETHUSDT filters, depth and next-funding observations on November 17, 2026. Its observations are read-only and become eligible at receipt. The 2024 manifest contains no borrowed filters. Historical spread is also absent, so the declared cost stress is a reconstruction and promotion evidence still needs coverage. [Pinned M0 capture selection](m0-capture.json) · [Binance market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
- Settlement times and rates are recorded primary observations. Inferred intervals and the archive's modeled arrival lags are reconstructions. Archive rows have no settlement mark. A live-held intrabar funding event in replay needs a funding-record mark, available from the public `fundingRate` response, or the run fails closed.

## Sample result

The daily probe uses the completed 60-day history, ranks on trailing 30-day average quote volume, and selects the side using signed 14-day return. It runs at 00:01 UTC to clear the declared availability lag. Stop-distance sensitivity scales `2.5 × ATR(14)` on the daily probe by 0.5×, 1× and 1.5×. Fee/slippage/funding-reserve sensitivity uses 1× and 2× the versioned modeled cost rates.

| Phase and equity | Candidate risk percentages | Per scenario attempted | Executable |
|---|---|---:|---:|
| Ignition 50 USDT | 8%, 10%, 12% | 113 | 0 |
| Growth 50 USDT | 1%, 1.5%, 2% | 113 | 0 |
| Growth 100 USDT | 1%, 1.5%, 2% | 113 | 0 |
| Growth 250 USDT | 1%, 1.5%, 2% | 113 | 0 |

| Candidate epoch | Positive momentum | Negative momentum | `MISSING_PIT_FILTER` | `LIQUIDATION_BUFFER` (tier absent) | `STALE_METADATA` |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 29 | 12 | 41 | 41 | 0 |
| ETHUSDT | 22 | 19 | 41 | 41 | 0 |
| DGBUSDT | 18 | 13 | 31 | 25 | 4 |

The report covers 8,136 attempts across 72 scenarios. The counts by reason can overlap. The report excluded one DGBUSDT symbol-day with unavailable funding and nine after delisting. Every attempted order has an unavailable critical input, so all 8,136 decisions carry `UNAVAILABLE` provenance. Executable size, notional, margin and risk utilization distributions are empty, and none of the stop or cost stresses changes that finding. The report status is **`INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE`**: no trade is possible under the available evidence, but the window establishes neither executability nor infeasibility at 50 USDT. Exchange-wide executability remains inconclusive.

Synthetic feasibility regression `test_t01_t20_t23_runtime_clock_report_and_config_identity` exercises 72 cases with known filter/tier inputs and a reconstructed spread. It passed 38 of 72 cases. The 50 USDT, 10% Ignition baseline is risk-bound with 1.5 units, 142.65 USDT notional, 14.265 USDT margin and 98.454% risk utilization. The 50 USDT, 2% Growth baseline fails the synthetic minimum notional and risk checks. T-41 separately verifies a margin-bound order below 100% risk utilization. These are sizing regressions on a synthetic symbol.
