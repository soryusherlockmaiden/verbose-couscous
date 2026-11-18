# M0 dynamic depth verification

`capture-depth-v1` on base `291b078`. The shipped OFFLINE configuration enables five-minute sampling of every trading USDT-quoted perpetual plus the configured symbols. SQLite schema 3 records the frozen targets, snapshot receipt and hash, selection or fallback reason, weight reservations, and coverage. The M1 reader accepts the new schema through the existing observation path. Its receipt ordering, content and selection digests, and spread calculation stay the same.

## Weight budget and storage

Binance's USD-M exchange information documentation shows a 2,400 request-weight limit per IP per minute and identifies its endpoint as weight 1. The published depth weight at limit 5 is 2, and the mark-price endpoint is weight 10 without a symbol. Funding information has weight 0 and a separate shared limit of 500 calls per five minutes per IP with funding-rate history. We conservatively charge funding information one local unit and keep its daily cadence. The collector reserves every attempted request against 1,200 weight in any rolling minute, including retries and requests interrupted before a response. This is the collector's budget on an IP, with a single collector advised. [Exchange information and limit](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information) · [General rate-limit rules](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) · [Depth](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Order-Book) · [Mark price](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price) · [Funding information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info)

At five-minute intervals there are 288 samples per day per symbol. The recorded five-level depth payload is 563 bytes raw and 177 zlib-compressed. This gives 48.6–64.9 MB raw or 15.3–20.4 MB compressed depth per day for 300–400 symbols. [SQLite growth estimate](#sqlite-growth-estimate) includes the other endpoints, slot journal and indexes. The actual current symbol count and payload sizes could not be measured from this host.

## Acceptance evidence

| Criterion | Result |
|---|---|
| Dynamic selection and coverage | Met on fixture. TRADING, quoteAsset and contractType apply independently. Index and pegged examples are included. Configured-only symbols are reported separately. |
| Point-in-time membership and safe fallback | Met on fixture. Missing, invalid, stale and inaccessible snapshots use the configured list. A newly arrived snapshot changes the next slot. Removal and addition affect only that next plan. |
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

## PR #6 prospective capture review

At `ea3c509`, a synthetic exchange snapshot captured inside a registered prospective holdout reduced the next slot to the configured symbols. Commit `4b3f9d2` first allowed that snapshot to plan a slot wholly within the same prospective range. The follow-up below extends this acquisition rule. The retrospective reserve remains sealed even when the fixture also registers that period as a prospective holdout. The restriction is checked before selecting the snapshot BLOB. A holdout-derived depth response arriving at or after slot end records a missed sample without a payload.

The recorder's slot plans, targets and coverage are sealed operational metadata for any slot or source snapshot touching a registered range. Keep those journal rows outside research inputs. A query-traced regression verifies that the shared history loader skips prospective payloads and leaves the journal alone. T-15 research entry points use the same receipt and asset checks as before. No market request was needed to reproduce the issue or verify the follow-up. [P2 review](https://github.com/soryusherlockmaiden/verbose-couscous/pull/6#discussion_r4613749166)

The prospective tests reproduced the fallback on the old code. The focused follow-up passed 9 cases, and the full suite passed 264. The explicit capture, M2 data and import-contract run passed 132 cases. Both pairs of smoke digests matched the original records above. Ruff, mypy and Compose config passed. The local Docker socket denied the build again. [PR checks](https://github.com/soryusherlockmaiden/verbose-couscous/pull/6/checks)

```sh
.venv/bin/python -m pytest -q tests/test_capture.py -k 'prospective or dynamic_fallback'
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -q
uv run --locked pytest -q tests/test_capture.py tests/test_m2_data.py tests/test_import_contracts.py
smoke_dir=$(mktemp -d /tmp/acgs-review6-smoke.XXXXXX)
for run in 1 2; do
  .venv/bin/python -m acgs --db "$smoke_dir/dynamic.sqlite3" --config config/m0.toml smoke
  .venv/bin/python -m acgs --db "$smoke_dir/legacy.sqlite3" smoke
done
git diff --exit-code ea3c509 -- PRD.md evidence/m1 tests/fixtures src/acgs/history_seal.json src/acgs/history_io.py src/acgs/m2_data.py config/m0.toml
git diff --check
docker compose config --quiet
docker compose build acgs
```

## SQLite growth estimate

Two new schema-3 databases were populated with one simulated UTC day each. For every slot, the experiment inserted the recorded depth fixture for each symbol, one compressed all-symbol `premiumIndex` sample, and one slot journal row containing the expected and target lists. It also inserted one `exchangeInfo` and one `fundingInfo` snapshot per day. Symbol names and timestamps were synthesized. Five price and rate fields in the premium snapshot used generated decimal strings with seed 2026. The ledger retained the standard checksums, ISO timestamps and indexes, including `capture_depth_slot`. File bytes were measured after commit and close.

| Symbols | Capture rows | Compressed payload bytes | Of which `premiumIndex` bytes | SQLite bytes |
|---|---:|---:|---:|---:|
| 300 | 86,690 | 18,806,563 | 3,509,988 | 86,609,920 |
| 400 | 115,490 | 25,045,069 | 4,649,949 | 115,503,104 |

That is about 87–116 MB/day or 32–42 GB/year. The operating estimate from the original README was about 150 KB raw per live all-symbol premium snapshot. Live compression ratios, symbol counts, retries, journal files, backups and filesystem use can change deployment growth. Measure database growth and free disk during the first days on the host. [P3 review](https://github.com/soryusherlockmaiden/verbose-couscous/pull/6#discussion_r4613750751)

## PR #6 UTC boundaries and capture continuity

The operator requires the start and end of every registered retrospective reserve and prospective holdout to be exactly `00:00:00.000 UTC`. The seal refuses unaligned boundaries before payload reads or network requests. The committed registry and aligned fixture ranges load unchanged. T-15 regressions check four invalid boundary cases through M1 historical, feasibility, replay, the loader and fetch script, M2 coverage, loader and fetch, and both modes of M0 capture. Audit hooks guard files and requests, and SQLite tracing guards capture BLOB reads.

M0 retains dynamic depth targets at the end of a prospective holdout, including when a slot straddles the boundary. The snapshot and slot must be outside all retrospective reserves. Tests verify continued suppression of late depth payloads for holdout-derived slots and that M1 reads only unsealed receipts without reading the slot journal. The four invalid registry cases and two capture-continuity cases failed on `82b9b29` and pass with this follow-up.

Local uv sync, Ruff and mypy passed. The full suite passed **271** cases. The explicit capture, M2 data and import-contract run passed **139** cases. Legacy and dynamic smoke digests reproduced twice. Two independent synthetic M2 audits matched byte for byte and reproduced manifest `35c72ed42f63a396605e7bbfb8b042694c59f086058af7d4cd0add0dc9ac9f00` and coverage `b2782e2f034d982ea08a3216c5747688c1cc2190de3cec20a734d4e854842de5`. Compose config passed, and the local Docker socket denied the build. Verification used fixtures. [PR checks](https://github.com/soryusherlockmaiden/verbose-couscous/pull/6/checks)

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest -q
uv run --locked pytest -q tests/test_capture.py tests/test_m2_data.py tests/test_import_contracts.py
dir=$(mktemp -d /tmp/acgs-midnight-check.XXXXXX)
for run in a b; do
  .venv/bin/python -m acgs --db "$dir/dynamic.sqlite3" --config config/m0.toml smoke
  .venv/bin/python -m acgs --db "$dir/legacy.sqlite3" smoke
  .venv/bin/python tests/m2_fixture.py "$dir/$run"
  .venv/bin/python -m acgs.m2_io --config "$dir/$run/m2.json" --manifest "$dir/$run/manifest.json" --assets "$dir/$run/assets" --purpose development coverage --policy config/m1.json --start 2000-01-01T00:00:00Z --end 2000-01-01T02:00:00Z > "$dir/$run/coverage.json"
done
cmp "$dir/a/manifest.json" "$dir/b/manifest.json"
cmp "$dir/a/coverage.json" "$dir/b/coverage.json"
sha256sum "$dir/a/manifest.json" "$dir/a/coverage.json"
git diff --exit-code 82b9b29 -- PRD.md 'evidence/m1/*.json' tests/fixtures src/acgs/history_seal.json config
git diff --check
docker compose config --quiet
docker compose build acgs
```

## Capture-depth-v2

Base `c0957fe`, implementation `7c347a3` and `c4cf6fa`. Tests used synthetic capture receipts, two independent M2 fixture directories, and the locally available checksum-pinned 2024 M1 assets. There were no Binance requests. The registry, M1 manifests, canonical fixtures, policy and replay semantics retain their accepted pins.

| Criterion | Evidence |
|---|---|
| SC-01, T-35, capture exclusions | A 522-name snapshot imports 521 supported epochs. The unsupported name is counted twice (metadata and its book). Unordered levels produce a zero spread at a locked best price. Empty, crossed and extreme books have separate exclusions. The database bytes and selection hash stay identical. After a seal or long gap, all three dependent endpoint types lose the prior identity. A fresh snapshot restores attribution for the new epoch. |
| ROADMAP §3, operational coverage | A final late HTTP 200 produces exit 1 and a closed-slot summary. Watch mode logs the same result. At 85 seconds per depth GET, three of six expected symbols miss each five-minute slot. Over four slots the missed sets alternate, with zero consecutive misses for any symbol. Source cooldown survives a UTC midnight and restart. |
| T-15, T-18 | Both REST kinds reject a filename for the wrong range before a payload open. The three accepted M1 sidecars load through their legacy pins. The synthetic T-18 file uses a range-bearing name and retains the original settlement-matching assertions. Malformed registry fixtures stop capture before GET. |
| M2 Phase A output | Independent Windows fixture audits each produced manifest SHA-256 `35c72ed42f63a396605e7bbfb8b042694c59f086058af7d4cd0add0dc9ac9f00` and coverage file SHA-256 `b2782e2f034d982ea08a3216c5747688c1cc2190de3cec20a734d4e854842de5`. |
| T-13/T-16, tests and packaging | Windows and Linux Docker each passed all 290 tests, Ruff and strict mypy (26 files). The explicit import-contract run passed 46. Each config reproduced its previously recorded smoke digest twice. Both Docker targets built and the runtime smoke recorded and replayed in a dedicated volume. Pinned Gitleaks found zero leaks in all 91 commits through the code changes. |

Running the 19 new regression cases against the exported base source yielded 16 failures and three existing refusal behaviors. All 19 passed on the branch. The canonical historical, feasibility, empty-window and replay output digests reproduced as `920427b4…`, `b4858aa2…`, `6f1ab1e5…` and `67da3e70…` respectively. A raw Git blob comparison against main matched all 19 files under `evidence/m1`, `tests/fixtures`, `config`, the seal JSON, `pit.py`, `broker_sim.py`, `funding.py` and `sim_config.py`.

Local verification used Python 3.12.13, an isolated output directory (`$audit`) and the following commands. The four public M1 runs use the accepted dates and flags from [M1 verification](../m1/verification.md).

```powershell
uv sync --locked
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest -q --basetemp="$audit/final-full"
.\.venv\Scripts\python.exe -m pytest -q tests/test_import_contracts.py --basetemp="$audit/imports"
.\.venv\Scripts\python.exe -m pytest -q -k hardening --basetemp="$audit/green"
foreach ($run in 1,2) { .\.venv\Scripts\python.exe -m acgs --db "$audit/dynamic.sqlite3" --config config/m0.toml smoke }
foreach ($run in 1,2) { .\.venv\Scripts\python.exe -m acgs --db "$audit/legacy.sqlite3" smoke }
docker compose config --quiet
docker compose -p acgs-hardening-check --profile check build acgs check
docker compose -p acgs-hardening-check --profile check run --rm check
foreach ($run in 1,2) { docker compose -p acgs-hardening-check run --rm acgs smoke }
docker run --rm --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo --mount "type=bind,source=C:\dev\acgs,target=/repo,readonly" ghcr.io/gitleaks/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055 git --redact --log-opts=--all /repo
```

The local PR #7 verification left deployment smoke and growth measurement for the VPS. The post-stop sizing contract stays in SC-02. Funding candidates outside 1h/2h/4h/8h, horizon-dependent trailing diagnostics, the M2 data-window lower bound and full-archive decompression cost remain outside this change.

## Deployment host smoke

An earlier independent audit on the operator workstation completed 522 on-time depth captures in about 161 seconds of a 300-second slot. The operator subsequently reported this slot from the VPS running `capture --watch` after PR #7:

```text
Capture slot=2026-11-18T15:55:00+00:00 state=closed expected=522 on_time=522 late=0 missed=0 fallback=none
2026-11-18T15:55:00+00:00 EXCHANGE_INFO {"captured": 522, "configured_only": {}, "expected": 522, "missed": {}}
SQLite: 0.71 MiB
```

The slot summary and read-only SQLite query agree. The 15:50 UTC bootstrap used the configured symbols and stored `exchangeInfo` for the next slot. The 0.71 MiB figure is the rounded baseline for this VPS database. VPS cycle duration, the 288-slot audit and measured daily growth remain pending. This smoke verifies public acquisition on the deployment host.
