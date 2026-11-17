# M1 verification

Branch `codex/m1-market-offline` from `origin/main` at `0903329ead42ed3097a14c4e8ba15c97157c2e5d`. The implementation commit is `7d8af7f20773e400362e45e90a974613f64441b4`. The implementation and evidence await independent review and operator acceptance. [50 USDT report](feasibility.md).

## Checks

| Environment | Commands and result |
|---|---|
| Windows native | `uv python install`, `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked mypy`, `uv run --locked pytest -q` passed. CPython 3.12.13, 135 tests. Import contracts ran in the suite. |
| SQLite regression | `uv run --locked python -m acgs --db data/m1/verification-smoke.sqlite3 smoke` twice recorded and replayed the same M0 state digest. M0 migration tests passed. |
| Docker | `docker compose config --quiet`, `docker compose --profile check build acgs check`, `docker compose run --rm acgs smoke` twice, and `docker compose --profile check run --rm check` passed. CPython 3.12.13, 135 tests. |
| Forward capture ingestion | `python -m acgs historical --manifest evidence/m1/m0-capture.json --assets data/m1/archive --capture-db data/m0-capture.sqlite3` yielded two epochs with one verified filter, one depth sample and two schedule observations each. The second schedule observation per symbol is the explicit unknown interval from absence in `fundingInfo`. No capture DB migration or mutation was needed. The raw local capture database is excluded from Git. |
| Real sample reproducibility | Ran the report command twice on Windows and ran `docker compose run --rm -v "${PWD}:/sample:ro" acgs feasibility --manifest /sample/evidence/m1/binance-2024.json --assets /sample/data/m1/archive --policy /app/config/m1.json --start 2024-03-01T00:00:00Z --end 2024-04-11T00:00:00Z --out /data/m1-feasibility-2024-v1.json`. Both environments emitted the same data and output digests in the report. |

No stochastic fills are used. The deterministic fixture entry/exit run using `config/m1.json` has run digest `58294990f235395a7b717006f9ccccaec2a83ad1951ddc51bf736fbe2a74176b` and canonical replay hash `3a4cbe6c11e66912fa1da3c56ce59c10d5fb87f5d8bcaa801907d811297f5b01`. The runtime regression compares artifacts with the wall clock disabled through direct and transitive module paths and uses a different ambient Decimal context.

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
