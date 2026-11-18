# M0 dynamic depth verification

`capture-depth-v1` on base `291b078`. The shipped OFFLINE configuration enables five-minute sampling of every trading USDT-quoted perpetual plus the configured symbols. SQLite schema 3 records the frozen targets, snapshot receipt and hash, selection or fallback reason, weight reservations, and coverage. The M1 reader accepts the new schema through the existing observation path. Its receipt ordering, content and selection digests, and spread calculation stay the same.

## Weight budget and storage

Binance's USD-M exchange information documentation shows a 2,400 request-weight limit per IP per minute and identifies its endpoint as weight 1. The published depth weight at limit 5 is 2, and the mark-price endpoint is weight 10 without a symbol. Funding information has weight 0 and a separate shared limit of 500 calls per five minutes per IP with funding-rate history. We conservatively charge funding information one local unit and keep its daily cadence. The collector reserves every attempted request against 1,200 weight in any rolling minute, including retries and requests interrupted before a response. This is the collector's budget on an IP, with a single collector advised. [Exchange information and limit](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information) · [General rate-limit rules](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) · [Depth](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Order-Book) · [Mark price](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price) · [Funding information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info)

At five-minute intervals there are 288 samples per day per symbol. The recorded five-level depth payload is 563 bytes raw and 177 zlib-compressed. This gives 48.6–64.9 MB raw or 15.3–20.4 MB compressed per day for scenarios of 300–400 symbols, excluding configured extras, SQLite overhead and other endpoints. The actual current symbol count and payload sizes could not be measured from this host.

## Acceptance evidence

| Criterion | Result |
|---|---|
| Dynamic selection and coverage | Met on fixture. TRADING, quoteAsset and contractType apply independently. Index and pegged examples are included. Configured-only symbols are reported separately. |
| Point-in-time membership and safe fallback | Met on fixture. Missing, invalid, stale and sealed snapshots use the configured list. A newly arrived snapshot changes the next slot. Removal and addition affect only that next plan. |
| Pacing and recovery | Met on fixture. 400 perpetuals plus two configured extras and three metadata requests complete within 102 seconds with 250 ms of modeled latency per request and a 1,200 weight budget. A second test uses 1 ms latency and a 600 weight budget and completes within 83 seconds. Both check every rolling minute. Interruption, retry/restriction stops, late receipts and expired gaps are tested. |
| M1 and import contracts (T-13, T-16) | Met on fixture. The existing importer returns spread observations for the requested symbols using schema 3. The M1 manifests, canonical fixtures and holdout seal are byte-identical to main. Full tests and import contracts passed. |
| Current public endpoint cycle | **BLOCKED on this host.** One actual GET to exchangeInfo returned HTTP 451 after about 300 ms. The collector latched, recorded one `ACCESS_RESTRICTED` result and stored zero payload bytes. It issued no depth or follow-up request. A complete live cycle remains unverified. |

No-config smoke and an explicit legacy config retain `e2fb3f83500b5aa748b93e0e3151cd9fc1536048d56692da249ed0e877362505`. The shipped `config/m0.toml` produces `35be0f1204fa4a70ff517a6643703bfbc657ee64b8cd0827682a183d3db7bc34`. The recorded and replayed digests matched for each config.

## Commands

Local Python 3.12.13 and uv 0.13.7. Ruff passed, mypy checked 26 source files, and pytest passed 260 tests. The targeted invocation passed 84 cases including 46 import contracts and 38 capture tests. The local Docker socket denied access. The PR workflow covers Ubuntu and Windows, both Docker builds, Docker check and two smokes, and pinned Gitleaks. Its results are attached to the PR.

```sh
export PATH="/tmp/m2-bin:$PATH" UV_CACHE_DIR=/tmp/m2-uv-cache UV_PYTHON_INSTALL_DIR=/tmp/m2-uv-python
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -q
uv run --locked pytest -q tests/test_import_contracts.py tests/test_capture.py
smoke_dir=$(mktemp -d /tmp/acgs-depth-smoke.XXXXXX)
for run in 1 2; do
  uv run --locked python -m acgs --db "$smoke_dir/dynamic.sqlite3" --config config/m0.toml smoke
  uv run --locked python -m acgs --db "$smoke_dir/legacy.sqlite3" smoke
done
git diff --exit-code origin/main -- PRD.md evidence/m1/m0-capture.json tests/fixtures src/acgs/history_seal.json
git diff --check
docker compose build acgs
capture_dir=$(mktemp -d /tmp/acgs-live-depth.XXXXXX)
.venv/bin/python -m acgs --db "$capture_dir/live.sqlite3" --config config/m0.toml capture
```
