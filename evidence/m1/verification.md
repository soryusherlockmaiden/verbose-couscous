# M1 verification

Branch `codex/m1-market-offline` from `origin/main` at `0903329ead42ed3097a14c4e8ba15c97157c2e5d`. The initial implementation commit is `7d8af7f20773e400362e45e90a974613f64441b4`. Claude recheck of the follow-up changes, fresh Codex review, and operator acceptance are pending. [PR #4](https://github.com/soryusherlockmaiden/verbose-couscous/pull/4) · [50 USDT report](feasibility.md).

## Initial implementation checks

| Environment | Commands and result |
|---|---|
| Windows native | `uv python install`, `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked mypy`, `uv run --locked pytest -q` passed. CPython 3.12.13, 136 tests. Import contracts ran in the suite. |
| SQLite regression | `uv run --locked python -m acgs --db data/m1/verification-smoke.sqlite3 smoke` twice recorded and replayed the same M0 state digest. M0 migration tests passed. |
| Docker | `docker compose config --quiet`, `docker compose --profile check build acgs check`, `docker compose run --rm acgs smoke` twice, and `docker compose --profile check run --rm check` passed. CPython 3.12.13, 136 tests. |
| Secret scan | The [README Gitleaks command](../../README.md#public-forward-capture) ran locally using the pinned image with `git --redact --log-opts=--all /repo`. No leaks found. The PR's `secrets` job repeats it. |
| Hosted CI | The initial PR head `27106e4` passed all four jobs. The PR checks track the last self-review commit. [Run 44199858421](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44199858421). |
| Forward capture ingestion | `python -m acgs historical --manifest evidence/m1/m0-capture.json --assets data/m1/archive --capture-db data/m0-capture.sqlite3` yielded two epochs with one verified filter, one depth sample and two schedule observations each. The second schedule observation per symbol is the explicit unknown interval from absence in `fundingInfo`. No capture DB migration or mutation was needed. The raw local capture database is excluded from Git. |
| Real sample reproducibility | Ran the report command twice on Windows and ran `docker compose run --rm -v "${PWD}:/sample:ro" acgs feasibility --manifest /sample/evidence/m1/binance-2024.json --assets /sample/data/m1/archive --policy /app/config/m1.json --start 2024-03-01T00:00:00Z --end 2024-04-11T00:00:00Z --out /data/m1-feasibility-2024-v1.json`. Both environments emitted the same data and output digests in the report. |

No stochastic fills are used. The deterministic fixture entry/exit run using `config/m1.json` has run digest `58294990f235395a7b717006f9ccccaec2a83ad1951ddc51bf736fbe2a74176b` and canonical replay hash `3a4cbe6c11e66912fa1da3c56ce59c10d5fb87f5d8bcaa801907d811297f5b01`. The runtime regression compares artifacts with the wall clock disabled through direct and transitive module paths and uses a different ambient Decimal context.

At Claude's review head the public replay persisted the PIT refusal and produced `6685126f290f98a4cc0f8c40a59151d1b5d699b1801b774a2a9846eb3955070d`. The current replay commands and hashes are in the follow-up below.

## ROADMAP §4 acceptance coverage

| Criterion | Checks |
|---|---|
| Identical replay, immutable inputs/output | T-01 passed, including archive checksum and captured raw hash validation, conflicting output refusal and repeated replay. |
| Future candle, epoch, filter and funding cannot affect a decision | T-02/T-42 passed, including filter receipt time and funding record arrival. |
| Critical missing inputs exclude a symbol-period | T-03/T-19/T-42 passed, including a gap in contiguous warm-up, missing funding history and absent metadata. |
| Quantization and exchange minima preserve risk | T-04/T-22/T-33/T-41 passed, including separate lot filters, actual fill gaps, and margin unexpectedly binding before risk. |
| Wallet, equity, fees, realized/unrealized PnL and partial fills reconcile | T-05/T-17 passed. |
| Funding ownership and ambiguity | T-18/T-19/T-36/T-42 passed, including holder at tied time, 8h→4h inference, authoritative settlements and missing data. |
| Gap-through-stop, missing marks/candles and delisting | T-19/T-21/T-35 passed. Mark proxy has explicit labels and opt-in. |
| Trailing and mark-based liquidation | T-37/T-38 passed, including both stop price types and liquidation priority. |
| OFFLINE mode and capability boundary | T-13/T-16/T-20 passed. An unapproved versioned candidate filled in the simulator. M0 refusal of PAPER and LIVE variants passed. |
| 50 USDT report | T-23 passed with the fixture grid and the real pinned dataset run. Real history gives conservative no-trade under the available PIT metadata. |

SC-01, SC-02 and SC-12 technical resolutions are in SPEC v0.5 §13.1. Historical filter, maintenance and spread coverage limit inference about actual executability and are reported separately. M0 account eligibility remains `UNVERIFIED` and the jurisdiction deadline remains before M3. SC-03–SC-05 remain due before the M2 holdout. The reviewed code, CI and exact commit are tracked on the pull request.

## Independent review (Claude, 2026-11-18 UTC)

Claude reviewed the full PR diff at head `0fd2ed00dda8fe4ac08776364faec1212d40f130` against `main` `0903329`. The findings and decisions at the time of that review are preserved below. Claude's resulting head `f5dbf2444681e9d3ab8e390040e04f48e3aefb1a` passed all four CI jobs. [Claude review head CI](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44204820759).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| R1 | High | With any positive candle availability lag, and both pinned manifests use `candle_delay_ms` 1000, every bar-close snapshot is ineligible for trigger evaluation and every `TRAIL` intent is rejected as `TRAIL_CLOSE_UNAVAILABLE` (`broker_sim.py`, `close_available`/`trail`). On real data, M2 would see no periodic eligible equity observation and could not model the step-wise ATR trailing exit (SPEC §4.5, §6, T-37). All three close snapshots of the public replay are `trigger_observable: false`. | **Open.** This changes SC-02 semantics, so it needs an operator decision before M1 acceptance. Options: apply an available-close ratchet after the next open's gap check and reject it if that open already crossed it; record each snapshot's availability time instead of a boolean; or require zero-lag manifests for M2. |
| R2 | Medium | 143 of the 999 archive settlements carry 1–15 ms `calc_time` offsets. Under the literal-timestamp rule (T-18 regression), they are intrabar for the bar opening on the hour. Archive rows have no settlement mark, so an exposed 15m/1h replay fails closed. With `fundingRest` marks, a settlement milliseconds after the open is charged to a position entered at that open. That resolves an unresolvable ordering deterministically, but not necessarily conservatively. | **Open** (SC-02 decision): a declared boundary tolerance for settlement timestamps, or mandatory REST marks. |
| R3 | Medium | A liquidation just past maintenance returned isolated margin (0.05345 of 10.01 USDT). | Fixed: the cash loss is at least the isolated margin plus credited funding, with the residual posted as `LIQUIDATION_CLEARANCE`. |
| R4 | Medium | An entry refused for unusable PIT inputs persisted no `EligibilityDecision` or taxonomy reason (SPEC §5.1). A partial fill stored only the full-order decision. | Fixed. |
| R5 | Medium | `PERCENT_PRICE` was applied to stop and trailing triggers. Binance defines it for a limit `price` only, so BTCUSDT's captured 0.97–1.03 band falsely rejected stops wider than 3% (T-41). | Fixed: stops face `PRICE_FILTER` and tick rules. The entry keeps its band check. |
| R6 | Medium | The feasibility status read `NO_TRADE_IN_DECLARED_WINDOW` while 8,136 of 8,136 attempts had `UNAVAILABLE` provenance. | Fixed: schema `m1-feasibility-v2` reports `INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE`. |
| R7 | Low | The listing boundary for held exposure used only the causal as-of view, so a status change with later evidence passed silently. | Fixed: effective time also fails the replay closed. |
| R8 | Low | Snapshot reconciliation compared totals kept by the posting loop itself and could not fail. | Fixed: each replay re-derives every snapshot from immutable postings. |
| R9 | Low | JSON floats were accepted for decimals (`risk_pct` 0.1 became 0.1000000000000000055…). | Fixed: decimal strings or integers only. |
| R10 | Low | Exit and trailing intents could be dropped without an event. Digest labels in the report read as file hashes. | Fixed. |

| Check (cloud agent, Ubuntu 24.04, uv 0.13.7, CPython 3.12.13) | Result |
|---|---|
| `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked mypy`, `uv run --locked pytest -q` | Passed; 140 tests, including the import contracts and the runtime wall-clock and Decimal-context determinism test |
| `uv run --locked python -m acgs --db data/m1/review-smoke.sqlite3 smoke` twice | Recorded, then replayed `e2fb3f83500b5aa748b93e0e3151cd9fc1536048d56692da249ed0e877362505` |
| `evidence/m1/fetch.py`, `historical`, then `feasibility` and `replay` twice each with the commands in this file and [feasibility.md](feasibility.md) | Archive checksums matched; dataset `44d93cb1…`; feasibility `09d90ebc…`, replay `6685126f…` on both runs; immutable `--out` accepted the identical bytes |
| Docker: `docker compose config --quiet`, smoke twice, `check` service, and `feasibility`/`replay` with `/sample` mounted read-only | Passed with the same smoke digest, 140 tests and identical output hashes. The images were built from the repository Dockerfile with a sandbox-only proxy CA layer, because this container intercepts TLS; the hosted `container` job builds the unmodified Dockerfile. |
| Pinned Gitleaks v8.24.3 (README command) | 44 commits scanned (all local refs, including this record), no leaks |

Not run here: the `--capture-db` ingestion, because no M0 capture database is in this container (`fapi.binance.com` returned HTTP 451 and was not bypassed); and Windows native, which is covered by the hosted `windows-2025` job. The real 2024 sample still has no point-in-time filters or BTC/ETH maintenance tiers, so SC-12 sizing and rejection behavior is exercised only on synthetic fixtures. Promotion-grade feasibility needs forward-captured filters. M1 acceptance remains the operator's decision.

## Follow-up on R1, R2 and feasibility status

| Finding | Verified follow-up |
|---|---|
| R1, lagged close and trailing | Snapshots now publish `trigger_available_at`. All three public close snapshots become eligible 1,000 ms after their timestamps. T-37 verifies a deferred update on the next eligible open, old-stop gap priority, a crossed proposed stop, and rejection when the original position has closed. |
| R2, funding at the bar boundary | All 999 archive settlements match a public REST rate and timestamp exactly and now carry settlement marks. T-18 verifies a held position posts an offset settlement once at the original time, the default 15 s ambiguity guard refuses entries and exits around funding, and delayed cash cannot size an entry before observation. Tests also retain the exact-clock time-priority sensitivity. |
| SC-12, zero or unusable attempts | The January 2 sample has zero attempts due to insufficient warm-up and returns `INCONCLUSIVE_NO_ASSESSABLE_ATTEMPTS`. Stale or disallowed critical metadata cannot establish an assessable rejection. The main sample still has 8,136 unavailable attempts and reports `INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE`. |

Windows native passed `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked mypy`, and `uv run --locked pytest -q` with 156 tests. Docker passed `docker compose config --quiet`, `docker compose --profile check build acgs check`, `docker compose run --rm acgs smoke` twice, and `docker compose --profile check run --rm check` with the same 156 tests. Native smoke also replayed twice with the recorded M0 digest. Read-only M0 capture ingestion was rechecked on Windows. Use the pinned Gitleaks command above or the PR `secrets` job.

```text
uv run --locked python -m acgs replay --manifest evidence/m1/binance-2024.json --assets data/m1/archive --policy config/m1.json --start 2024-03-01T00:00:00Z --end 2024-03-04T00:00:00Z --plan evidence/m1/replay-plan.json --out data/m1/replay-public-v4.json
uv run --locked python -m acgs feasibility --manifest evidence/m1/binance-2024.json --assets data/m1/archive --policy config/m1.json --start 2024-01-02T00:00:00Z --end 2024-01-03T00:00:00Z --out data/m1/feasibility-empty-v3.json
```

Windows and Docker reproduced canonical replay hash `86857766da69199345a7c4ed1bfcd2f7771841de8084771f7b397ab9fcbc0ee2` and [main feasibility hash](feasibility.md) `ee0714a60baf1576f816cffd928e01fc4b94b1c517c7013f006882118d4ad918`. Docker used the runtime service with the checkout mounted read-only at `/sample` and outputs in the named `/data` volume. The deterministic exact-clock fixture uses run digest `d0360b1ede9001d50b4564a1f9b16dce1c990e388072988332a813ec2cdd34c6` and replay hash `92a6e207f73bff49b5429ee02a5cd3cf504f05e1cfe206fe7b00f2ffa791b87e`. The new hashes reflect the versioned policy, dataset, and replay snapshot schema.
