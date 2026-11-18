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
| Holdout safety | Met in the Phase A tooling. | T-15 tests direct refusal, cross-boundary bar and funding dependencies, whole overlapping monthly archives in all three families, digest/format/scope refusals, and M1 legacy loader refusal. The tests intercept payload opens and HTTP requests. Holdout access requires a separately authorized future workflow. |
| SC-03, SC-04, SC-05 draft | Met for review. | [Protocol](protocol.md) and [config](../../config/m2.json), with nine operator decisions and proposed alternatives. |
| M1 accounting, event order and fixtures | Regression suite met. | Accepted M1 test suite passes, including the shared broker/funding helper. Canonical M1 JSON, policy and recorded raw fixtures are byte-identical to main. No M2 market replay ran. |
| Freeze readiness | Pending. | Real approved datasets, choices, implementation and development/validation evidence remain outstanding. |

| ID | Phase A evidence and remaining work |
|---|---|
| T-05 | M1 adverse cost and accounting regressions passed. M2 strategy integration and stresses belong to B/C. |
| T-06, T-07 | SC-05 specifies the fixed rule. B owns simulated path and latch regression. |
| T-14 | A records real coverage limitations, exclusions and proposed reconstruction bounds. O3 and C evidence remain open. |
| T-15 | Phase A tests passed for the supported fetch, research-load and coverage entry points. Direct, dependency and manifest refusals make zero held-out payload reads. Manifest and overlapping asset refusals make zero requests. |
| T-21 | M1 mark/proxy regressions passed. A coverage fixture tests missing recorded settlement marks and exact M1 mark matching. B/C own M2 labels and sensitivity. |
| T-27, T-30 | Proposed trigger consumption and settled headroom requirements are in the draft. B owns behavioral verification. |

## Commands and reproducibility

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
