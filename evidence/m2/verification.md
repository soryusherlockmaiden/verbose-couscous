# M2 Phase A verification

Base: main at `7c47fe38e9883f1cd84d4b45e2052bfb544cf114` (PR #4). Accepted execution: `m1-replay-v8`, `m1-v3`. Phase A review, operator acceptance, protocol freeze and the official holdout are separate events.

## Contract-to-work map

| ROADMAP §5 criterion | Owning phase and planned implementation, test or evidence |
|---|---|
| Versioned OFFLINE Volatility Breakout, 15m/1h, no pyramiding | B strategy emits candidates and provenance on actual entry points. Indexing, positive lag, broken warm-up, relisting and shared-prefix tests. |
| Freeze SC-04 signal timing, indexing, warm-up, stop levels and capital cadence | A draft and operator decision register. B feature, intent, ratchet and trigger tests. Freeze after C. |
| M1 delayed trailing and adverse-candidate funding | A records existing semantics and sensitivity design. B preserves ordered broker events and tests shared-prefix and single-epoch equivalence if extended. C reports paired lag and ownership cost diagnostics. |
| Major + Dynamic Top Movers, no-trade, random entry, ranking/filter ablations and calendar-block resampling | A acquisition/provenance audit and protocol. B PIT ranking and deterministic selection. C comparator and joint-block runner, effective sample and incremental results. |
| Chronological development/validation, walk-forward and untouched holdout | A proposed windows, checksum preflight and read seal. C designated walk-forward and validation while sealed. Official holdout follows freeze and explicit separate authorization. |
| 50 USDT feasibility and cost, regime, tail and parameter robustness | A coverage and exclusion audit. B M1 authorization on shared-wallet positions with reasons. C feasibility and robustness results by evidence tier, separate reference balance. |
| Minimal Ignition, target-before-halt, conditional feasibility and shortfall | A draft SC-05 and denominators. B M1 cycle equity, 50/100/30, headroom and sticky decisions; C modeled distributions, dead zone and lifetime recovery. |
| Preregister gates, zero/positive and exact operational reserve candidates, designate primary | A draft SC-03; C development calibration and proposed exact values. Operator freezes before holdout. |
| Develop only on designated inputs; freeze code, parameters, universe, costs and comparators | A access approvals and seals; B/C reproducible development and validation. Operator freeze decision after C. |
| Run holdout once, retain results and deliver evidence/verdict | Separate authorized holdout after freeze. C produces reproducible offline CLI and development/validation artifacts with code, data, config, protocol and seed pins. |
| Meet numeric gates, survive stresses, credible executable 50 USDT edge, robust to historical assumptions | Official holdout tests frozen gates. C reports development/validation evidence independently. Operator accepts milestone. |

| ID | Owning phase and planned verification |
|---|---|
| T-05 | A runs M1 regression suite. B checks adverse entry/exit, funding and gap treatment. C cost-stress and cost attribution. |
| T-06 | B proves 50→80→45 leaves the fixed halt clear. |
| T-07 | B proves equality at 30, eligible-only latch, and new-entry block. |
| T-14 | A records provenance, coverage and proposed reconstruction bounds. C runs sensitivities and applies the inconclusive rule. |
| T-15 | A direct/indirect load refusal before reads, full fetch preflight before requests, half-open ranges and separate manifests. C keeps seal active. Official holdout workflow belongs to separate authorization. |
| T-21 | A runs accepted M1 mark/proxy regressions and reports mark/settlement gaps. B preserves source labels. C evaluates proxy and liquidation sensitivity where authorized. |
| T-27 | B tests gross equity >=100 with cycle equity <100, then equality and eligible trigger sequencing. |
| T-30 | B verifies second authorization's effective budget and the specific `HALT_HEADROOM` refusal. A separate B regression verifies baseline concurrency. |

## Phase A results

| Phase A criterion | Status | Evidence |
|---|---|---|
| Historical data and provenance audit | Met for accepted M1 evidence and fixture. Real M2 inventory blocked by O1/O2. | [Data audit](data-audit.md) records each measured symbol-period and the historical filters, tiers, interval and spread limitations. |
| Checksum pins and deterministic manifest | Met on synthetic inputs. Market manifests blocked by O1/O2. | Seven ZIPs, 1,682 bytes, canonical manifest digest and two independent matching audits below. Real M2 transfer: 0 bytes. |
| Registered history seal | Verified in the supported OFFLINE M1 and M2 history entry points. | T-15 covers M1 historical, feasibility, replay, the shared loader and legacy fetch, and M2 fetch, research load and coverage. Tested refusals occur before payload opens or network requests. Capture selection skips sealed receipts before requesting BLOBs. Protocol freeze and holdout authorization remain separate. |
| SC-03, SC-04, SC-05 draft | Met for review. | [Protocol](protocol.md) and [config](../../config/m2.json), with nine operator decisions and proposed alternatives. |
| M1 accounting, event order and fixtures | Regression suite met. | Accepted M1 test suite passes, including the shared broker/funding helper. Canonical M1 JSON, policy and recorded raw fixtures are byte-identical to main. No M2 market replay ran. |
| Freeze readiness | Pending. | Real approved datasets, choices, implementation and development/validation evidence remain outstanding. |

| ID | Phase A evidence and remaining work |
|---|---|
| T-05 | M1 adverse cost and accounting regressions passed. M2 strategy integration and stresses belong to B/C. |
| T-06, T-07 | SC-05 specifies the fixed rule. B owns simulated path and latch regression. |
| T-14 | A records real coverage limitations, exclusions and proposed reconstruction bounds. O3 and C evidence remain open. |
| T-15 | The Phase A follow-up tests verify zero payload opens and network requests for refused M1 and M2 archive operations, and receipt-time filtering before capture BLOB selection. The independent registry covers the 2025 reserve. The prospective window is OPEN. |
| T-21 | M1 mark/proxy regressions passed. A coverage fixture tests missing recorded settlement marks and exact M1 mark matching. B/C own M2 labels and sensitivity. |
| T-27, T-30 | Proposed trigger consumption and settled headroom requirements are in the draft. B owns behavioral verification. |

## Initial commands and reproducibility

Linux, CPython 3.12.13, uv 0.13.7. The source and fixture tests were verified at `edbb520`. Native Ruff and mypy passed (24 source files); pytest passed **212** tests, including **16** Phase A tests. The separate import-contract invocation passed **46** tests.

```sh
export PATH="/tmp/m2-bin:$PATH" UV_CACHE_DIR=/tmp/m2-uv-cache UV_PYTHON_INSTALL_DIR=/tmp/m2-uv-python
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked pytest tests/test_import_contracts.py -q
git diff --exit-code origin/main -- 'evidence/m1/*.json' config/m1.json tests/fixtures
git diff --check origin/main
```

The following generated independent synthetic manifests and coverage reports. Both comparisons passed byte-for-byte. File digests include the final LF. The canonical manifest pin omits it. Fetch verified all local fixture bytes without a network request. The shipped draft refused acquisition at the open-approval check. The M1 script refused the M2 schema.

```sh
for run in a b; do
  uv run --locked python tests/m2_fixture.py "data/m2/fixture-$run" || exit
  uv run --locked python -m acgs.m2_io --config "data/m2/fixture-$run/m2.json" --manifest "data/m2/fixture-$run/manifest.json" --assets "data/m2/fixture-$run/assets" --purpose development coverage --policy config/m1.json --start 2000-01-01T00:00:00Z --end 2000-01-01T02:00:00Z > "data/m2/fixture-$run/coverage.json" || exit
done
sha256sum data/m2/fixture-{a,b}/{manifest,coverage}.json
cmp data/m2/fixture-{a,b}/manifest.json
cmp data/m2/fixture-{a,b}/coverage.json
uv run --locked python -m acgs.m2_io --config data/m2/fixture-a/m2.json --manifest data/m2/fixture-a/manifest.json --assets data/m2/fixture-a/assets --purpose development fetch
uv run --locked python -m acgs.m2_io --config config/m2.json --manifest data/m2/fixture-a/manifest.json --assets data/m2/fixture-a/assets --purpose development fetch
uv run --locked python evidence/m1/fetch.py data/m2/fixture-a/manifest.json data/m2/fixture-a/assets
```

| Artifact | Matching SHA-256 |
|---|---|
| Fixture manifest file | `134d08103fbcadad62fa59f86096542426349a396b3aedc2b0c2fb6f17ce3247` |
| Canonical fixture manifest pin | `14d9953d64221b805f0033a2d4cc77ab6161852da93476d3bff5799266292757` |
| Coverage file | `3196e05d415a5844249af4508926108e9c0c9e733b8eb9b2516198e0eacb0545` |

Docker Compose configuration passed. The first unmodified runtime build failed at PyPI TLS verification in the managed proxy. A temporary Dockerfile added a BuildKit CA secret to each existing `uv sync` command. The repository Dockerfile is unchanged. Both targets built with the session CA. The check container passed Ruff, mypy, and **212** tests. Smoke recorded and replayed digest `e2fb3f83500b5aa748b93e0e3151cd9fc1536048d56692da249ed0e877362505`.

```sh
docker compose config --quiet
docker compose build acgs
python - <<'PY'
from pathlib import Path
source = Path("Dockerfile").read_text(encoding="utf-8")
patched = source.replace("RUN uv sync", "RUN --mount=type=secret,id=proxy_ca,required=true SSL_CERT_FILE=/run/secrets/proxy_ca uv sync")
Path("/tmp/m2.Dockerfile").write_text(patched, encoding="utf-8")
PY
chmod a+r config/m0.toml config/m1.json config/m2.json
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -f /tmp/m2.Dockerfile --target runtime -t acgs:m0 .
docker compose -p acgs-m2-phase-a-final run --rm acgs smoke
docker compose -p acgs-m2-phase-a-final run --rm acgs smoke
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -f /tmp/m2.Dockerfile --target check -t acgs:m0-check .
docker compose --profile check run --rm check
```

Ran pinned Gitleaks v8.24.3 over 70 commits through the source head. No leaks found.

```sh
docker run --rm --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo --volume "$PWD:/repo:ro" ghcr.io/gitleaks/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055 git --redact --log-opts=--all /repo
```

Run the native Windows and standard Docker build in hosted CI. The Phase A checks establish fixture reproducibility and the access boundary. Market sizing, validation, and a milestone verdict require their own evidence.

## Phase A review follow-up on `67b536a`

The operator authorized a versioned seal change for accepted M1 tooling. The shared `history-seal-v1` check refuses registered ranges by full URL bundle, inclusive REST query bounds converted to half-open UTC, and capture receipt. M2 approvals must cover the independent registered ranges and Phase A refuses FROZEN configs. Coverage pins `m1-semantics-v1` and produces the same report when only the M1 halt buffer changes. The [protocol](protocol.md) records the operator's O1 direction and explicit OPEN O5 values. P3-2 and P3-3 are documented as due before Phase C.

| Files | Purpose |
|---|---|
| `history_seal.json`, `history_seal.py` | Package the registered 2025 reserve and verify source bundles and manifest metadata. |
| `history_io.py`, `research_io.py`, `evidence/m1/fetch.py` | Check the seal before payload access, block sealed M1 decision requests, and filter capture receipts before selecting BLOBs. |
| `m2_data.py`, `m2_io.py`, `m2_coverage.py` | Require registry coverage, DRAFT authorization and dated tier sources, and report the semantic pin. |
| `m2_semantics.py`, `funding.py`, `config/m2.json` | Pin accepted replay, accounting and funding semantics while leaving research settings open. |
| `tests/m2_fixture.py`, `tests/test_m2_data.py`, `tests/test_m1.py`, `tests/test_import_contracts.py` | Add audit-hook and capture query regressions, use canonical synthetic archive metadata and preserve import restrictions. |
| `Dockerfile`, `.dockerignore` | Include the legacy fetch script in the check image for its entry-point regression. |
| `SPEC.md`, `ROADMAP.md`, `evidence/m2/*.md` | Record the operator decisions, common seal and verification scope. |

Native Ruff and mypy passed with 26 source files. Pytest passed **222** tests, including **76** M1 tests, **46** import-contract tests and **26** Phase A tests. The canonical M1 manifests, policy and raw fixtures match `main` byte-for-byte. The seal metadata preflight passed for the 33-asset public manifest, the 36-asset enriched manifest and the 2026 capture selection. The M1 public replay hash `67da3e706e6594137fb4e3bd19db5ba2363dc18e1954a73219035cf271372929` and main feasibility hash `b4858aa261a01fcb747a2fcc5c05e7c671325e1a56d59dd2355abe9426ba8bcb` are prior evidence. Fresh reproduction is **BLOCKED** by the Phase A instruction to avoid market data. [Accepted M1 verification](../m1/verification.md)

```sh
export PATH="/tmp/m2-bin:$PATH" UV_CACHE_DIR=/tmp/m2-uv-cache UV_PYTHON_INSTALL_DIR=/tmp/m2-uv-python
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked pytest tests/test_m1.py tests/test_import_contracts.py -q
git diff --exit-code origin/main -- 'evidence/m1/*.json' config/m1.json tests/fixtures PRD.md
git diff --exit-code 67b536a -- src/acgs/broker_sim.py src/acgs/sim_config.py src/acgs/feasibility.py config/m1.json
git diff --check
.venv/bin/python - <<'PY'
from pathlib import Path
from acgs.history_io import read_json
from acgs.history_seal import registered_seal

seal = registered_seal()
for name in ("binance-2024.json", "binance-2024-marks.json", "m0-capture.json"):
    manifest = read_json(Path("evidence/m1") / name)
    seal.check_manifest(manifest)
    print(f"{name}: seal metadata preflight passed ({len(manifest['assets'])} assets)")
PY
```

Two independent synthetic runs reproduced the manifest file hash `35c72ed42f63a396605e7bbfb8b042694c59f086058af7d4cd0add0dc9ac9f00` and the coverage file hash `85b6ff43b4c2f593240357facc220a02de377357ca8703eca1166eca5f490a23`.

```sh
fixture_dir=$(mktemp -d /tmp/m2-seal-audit.XXXXXX)
for run in a b; do
  uv run --locked python tests/m2_fixture.py "$fixture_dir/$run"
  uv run --locked python -m acgs.m2_io --config "$fixture_dir/$run/m2.json" --manifest "$fixture_dir/$run/manifest.json" --assets "$fixture_dir/$run/assets" --purpose development coverage --start 2000-01-01T00:00:00Z --end 2000-01-01T02:00:00Z > "$fixture_dir/$run/coverage.json"
done
cmp "$fixture_dir/a/manifest.json" "$fixture_dir/b/manifest.json"
cmp "$fixture_dir/a/coverage.json" "$fixture_dir/b/coverage.json"
sha256sum "$fixture_dir/a/manifest.json" "$fixture_dir/a/coverage.json"
```

The normal local Compose build hit the managed proxy's PyPI CA error. Both image targets built with the session CA supplied as a BuildKit secret. An intermediate build passed **222** container tests, and its two smokes recorded and replayed digest `e2fb3f83500b5aa748b93e0e3151cd9fc1536048d56692da249ed0e877362505`. The final local rebuilder produced both images but ran out of disk while creating the first smoke container. Hosted CI on `5f68ede` passed both standard Compose builds, both Docker smokes, Docker check with **222** tests, and the Ubuntu and Windows native jobs. [Hosted PR run](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44272128130)

```sh
docker compose config --quiet
docker compose --profile check build acgs check
sed 's|RUN uv sync|RUN --mount=type=secret,id=proxy_ca,required=true SSL_CERT_FILE=/run/secrets/proxy_ca uv sync|' Dockerfile | docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -f - --target runtime -t acgs:m0 .
sed 's|RUN uv sync|RUN --mount=type=secret,id=proxy_ca,required=true SSL_CERT_FILE=/run/secrets/proxy_ca uv sync|' Dockerfile | docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -f - --target check -t acgs:m0-check .
docker compose -p acgs-m2-seal run --rm acgs smoke
docker compose -p acgs-m2-seal run --rm acgs smoke
docker compose --profile check run --rm check
docker compose -p acgs-m2-seal-final run --rm acgs smoke
```

After removing superseded task build artifacts and the task's rebuildable download cache, the local pinned Gitleaks scan completed. Local and hosted scans at `5f68ede` each reported **75** commits and **zero leaks**. [PR checks](https://github.com/soryusherlockmaiden/verbose-couscous/pull/5/checks)

```sh
docker run --rm --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo --volume "$PWD:/repo:ro" ghcr.io/gitleaks/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055 git --redact --log-opts=--all /repo
gh pr checks 5 --watch --interval 10
```

Phase A is reviewable with synthetic evidence. The public hash reruns, exact windows, separate coverage checks and approved market manifests remain pending. SC-03 through SC-05 remain draft and P3-2/P3-3 are due before Phase C.

## Second review follow-up on `cae4dbf`

Independent review found two P3 gaps. Both are fixed with regressions that fail on `cae4dbf`.

| Gap | Fix | Tests |
|---|---|---|
| Inline metadata observed after a sealed range could still describe that range, for example a delisting effective in 2025 recorded in 2026. | `HistorySeal.check_manifest` refuses inline metadata whose `effective` or `next_time` falls in a registered range, or whose `start`/`end` or `effective`/`until` span overlaps one. Onboard instants remain identity anchors. | `test_t15_later_evidence_cannot_describe_a_sealed_period` (4 cases, loader and `historical` CLI); `test_t15_sealed_onboard_identity_stays_admissible` |
| M2 refused any data window after the start of the holdout envelope, so evidence between the 2025 reserve and a later prospective holdout could never be approved. | M2 checks the approved data window against every registered range and keeps the configured holdout as an ordering guard. A config still cannot unseal a registered range. | `test_t15_m2_approves_data_between_a_registered_range_and_the_holdout`; `test_t15_m2_config_cannot_unseal_the_registered_reserve` |

Linux, uv 0.13.7: Ruff passed, mypy passed (26 source files), pytest passed **228** tests. Canonical M1 manifests, policy, raw fixtures and the M1 engine modules are unchanged. The seal preflight passed for the three M1 manifests. The synthetic fixture manifest and coverage hashes are unchanged from the first follow-up. The public M1 hash reruns remain pending operator authorization to re-fetch the pinned 2024 archives.

## Third review follow-up on `8a6bcd2`

Operator review found a P2 regression and a P3 inconsistency in the second follow-up. Both are fixed with regressions that fail on `8a6bcd2`.

| Finding | Fix | Tests |
|---|---|---|
| P2: removing the covering rule let the config move the chronology guard. A config could truncate the reserve, and with a registered prospective holdout a config moved later admitted development data after that holdout ended. | Registered ranges carry a `purpose`, and an unknown purpose is refused. M2 refuses a configured holdout that truncates a registered range or starts after a registered `m2-prospective-holdout`, and refuses approved data ending after a registered holdout starts. Data after the 2025 reserve stays admissible. | `test_t15_config_cannot_shift_research_chronology` (3 cases, loader and fetch CLI); `test_t15_holdout_registration_keeps_prior_development_valid` |
| P3: a pre-seal `premiumIndex` forecast whose next funding time is the first sealed instant loaded through the M0 capture path but was refused inline. | One rule for both paths: evidence observed at or after a range begins cannot describe an event time or span inside it. Earlier forecasts stay admissible. | `test_t15_pre_seal_forecast_is_admissible_on_both_input_paths` (inline and capture); `test_t15_later_evidence_cannot_describe_a_sealed_period` still refuses backfills |

Linux, uv 0.13.7: Ruff passed, mypy passed (26 source files), pytest passed **234** tests. The seal preflight passed for the three M1 manifests.
