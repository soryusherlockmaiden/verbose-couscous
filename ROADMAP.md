# ACGS — Engineering Roadmap

**Version:** 0.4 — Pre-M0 Corrections  
**Date:** 2026-11-17  
**Status:** Engineering baseline candidate; no milestone accepted  
**Basis:** `PRD.md` v1.4 (**Approved & Locked**) and `SPEC.md` v0.4  
**Execution principle:** Research-first, risk-before-order, evidence-based stop/go.

## 1. Delivery rules

1. The goal is **not** to ship a trading bot at any cost; the goal is to determine whether a 50 USDT Binance USD-M strategy is executable and has credible net-of-cost edge, then build safe operation only if justified.
2. `OFFLINE` by default. M0–M4 must never place real orders or real transfers. No testnet is used: M4 runs as mainnet shadow mode and M5 starts with `LIVE_SHADOW` (SPEC §3.1). M5 is optional and requires separate approval.
3. Milestones require **working code, reproducible tests, and concise evidence**; no governance-only milestone or mandatory standalone CANON/ADR/audit packs.
4. A milestone may report `PASS`, `FAIL`, `INCONCLUSIVE`, or `BLOCKED`. `FAIL`/`INCONCLUSIVE` do not permit live promotion. `BLOCKED` requires resolving dependencies before proceeding.
5. The untouched holdout is opened only after research protocol and numeric promotion thresholds are frozen.
6. Changes to PRD product baselines require explicit review; technical detail belongs in SPEC, progress in ROADMAP.
7. Stopping after M2 (no edge) or after M4 (paper only) is a legitimate, successful outcome (PRD §12.4).

## 2. Milestone map

| Milestone | Goal | Key dependency | Exit outcome |
|---|---|---|---|
| **M0 Foundation** | Reproducible, safe offline repo; account eligibility; forward data capture | PRD v1.4 baseline | CI, tests, OFFLINE-by-default, eligibility recorded |
| **M1 Market & Offline Execution** | Trustworthy data, cost-aware replay and **simulated equity accounting** | M0 | PIT universe, deterministic simulator, reconciled wallet/equity and event-order regression tests |
| **M2 Strategy Research** | Test whether breakout has credible edge at 50 USDT | M1; preregistered gates before holdout | `PASS`/`FAIL`/`INCONCLUSIVE`; may stop project |
| **M3 Capital & Risk Engine** | Implement safety and capital lifecycle | M2 strategy evidence for promotion; M1 contracts | State machine, transfer simulation, fault tests |
| **M4 Paper Pilot** | Prove end-to-end operation without real funds | M3 | Stable paper operation and reconciled safety |
| **M5 Optional Live Pilot** | Carefully authorize real operation | All prior gates passed | Manual go/no-go, controlled live deployment |

**Dependency nuance:** Design/prototyping of M3 components can occur earlier, but full M3 promotion should not be used to bypass a failed M2 research gate. M2 includes only a **minimal simulated Ignition policy**, not the complete M3 capital system.

## 3. M0 — Foundation

**Scope**
- Python 3.12+ project, typed modules, cross-platform dependency lock, lint/type/test commands. Identical results on Ubuntu 24.04, Windows (WSL2 or native) and CI, with Docker as the canonical execution path; CI runs lint/type/tests on Ubuntu and Windows.
- Docker Compose for local/VPS deployment; configuration schema and `OFFLINE` default.
- SQLite single-writer storage skeleton, deterministic clock and test fixtures.
- GitHub Actions: unit tests, lint, secret scanning; no production credentials in CI.
- Clear separation of offline, paper, and future live adapters; no live adapter implementation required.
- Import contracts enforcing the capability boundary (SPEC §2.1).
- **Account eligibility pre-check** (manual, no trading credentials): confirm the operator's account and jurisdiction permit Binance USD-M Futures, isolated margin, a dedicated account or sub-account, the needed API permissions (read-only if configured for PAPER, trading, universal transfer, IP restriction) and availability of the futures test-order endpoint. Each applicable item is recorded as `VERIFIED` (dated evidence), `NOT_ELIGIBLE` or `UNVERIFIED`.
  - Any `NOT_ELIGIBLE` ⇒ project `BLOCKED` before M1.
  - `UNVERIFIED` items do not block M0 acceptance or OFFLINE M1/M2. They are carried forward as open blockers with the deadlines below.
  - HTTP 451/403 from forward capture to Binance Futures public endpoints flags the jurisdiction item for operator review. It is not automatically `NOT_ELIGIBLE`, because only the host IP may be restricted.
- **Forward capture** (credential-free): scheduled job recording daily `exchangeInfo`/`fundingInfo` snapshots, periodic `premiumIndex` snapshots (all symbols) and periodic order-book/spread samples with provenance (SPEC §4.3, §4.3.1).

**Eligibility verification deadlines:**

| Item | Must be `VERIFIED` before |
|---|---|
| USD-M Futures access in the operator's jurisdiction | Start of M3 |
| Dedicated account/sub-account | Start of M4 |
| Read-only key permissions and account settings (single-asset mode, BNB fee deduction off, isolated margin), if PAPER uses an account key | Start of M4 |
| Live account settings; trading, universal-transfer and IP-restriction permissions; futures test-order endpoint | Start of M5 |

Verify all applicable items at the start of M5, including re-verification of items checked earlier.

**Deliverables:** runnable offline skeleton, CI workflow, README quickstart, config validation, first invariant tests, account eligibility record, running forward-capture job.

**Acceptance:** clean install/replay smoke test; CI green; secret scan clean; invalid config fails safely; no real exchange order/transfer path exists; restart preserves state fixture; import contracts enforced in CI (T-13); eligibility recorded (each applicable item has a status; `UNVERIFIED` allowed and listed as open); forward capture stores provenance-tagged snapshots.

**Exit decision:** accept M0 only when all checks are reproducible on a clean environment, and lint/type/tests pass on both Ubuntu and Windows CI runners.

## 4. M1 — Market & Offline Execution

**Scope**
- Historical OHLCV, funding, instrument status/filter ingestion with provenance. Instruments keyed by listing epoch; funding posted from recorded settlement events, with intervals derived and provenance-tagged (SPEC §4.3.1); historical mark-price kline coverage assessed before any proxy is used (SPEC §4.2/§4.5).
- Point-in-time symbol discovery including delisted symbols when evidence permits; missingness tags `VERIFIED`/`RECONSTRUCTED`/`UNAVAILABLE`.
- Universe snapshots: 30-day quote-volume/top-30 and 14-day signed momentum as configurable **research candidates**.
- Time-appropriate eligibility: contiguous history, min notional/quantity/step/tick, 50 USDT margin/stop-risk feasibility.
- Deterministic offline broker: order lifecycle, fees, funding, slippage/stress costs, partial fills and no-lookahead event sequencing.
- **M1 simulated equity accounting (SPEC §4.5 / SC-02):** wallet balance, realized/unrealized PnL, fee/funding postings, mark/source proxies, margin reservation, snapshot timestamps and exact equity reconciliation. Freeze event priority and mark/snapshot sampling rules **before M1 acceptance**; M1 owns all simulator event processing and M2 does not redefine it (SPEC §4.5.1). Exchange account reconciliation remains M3.
- Allow **versioned unapproved research candidates in OFFLINE simulation** with simulated risk checks and no real side effects; paper/live approval is separate.
- Immutable dataset/config hashes and replay outputs.
- Explicit `EligibilityDecision` with deterministic rejection reasons and a **50 USDT Execution Feasibility Report** (SPEC §5.1 / SC-12); distinguish verified from reconstruction-dependent feasibility. Cover Ignition and Growth risk levels and report the binding constraint and risk-utilization distribution (SPEC §5.2); margin-bound orders below 100% risk budget count as executable.

**Deliverables:** PIT datasets, provenance report, universe snapshots, deterministic broker/backtester, simulated wallet/equity ledger and event snapshots, exclusion reason report, **50 USDT Execution Feasibility Report**.

**Acceptance tests**
- Identical dataset/config/seed → identical output.
- Future candle/listing/filter/funding data cannot affect past decisions.
- Missing critical data excludes symbol-period or marks evidence inconclusive as applicable.
- Minimum-order rounding cannot breach modeled risk budget.
- Ledger invariant: `sim_equity = sim_wallet + unrealized_PnL`; realized PnL, fees and funding post exactly once, including partial/complete closes.
- **Event-order regression:** funding immediately before/after entry/exit, at open, intrabar stop/funding ordering and equal timestamps; replay is deterministic under the declared priority.
- Gap-through-stop, unavailable mark/candle for an active position, and delisting boundaries fail closed or follow a specified conservative rule; no silent stale mark.
- Historical mark proxies are explicitly labeled and tested; no claim that OHLC close equals actual exchange mark price.
- Unapproved versioned research strategy can execute **only in OFFLINE simulation**; no implicit PAPER/LIVE approval.
- No exchange credentials or live orders required.
- Candidate order rejected when satisfying minimum notional would breach risk after quantity rounding; margin/leverage does not waive stop-risk or reserve checks.
- Stop distance preserved from the actual fill and tick rounding never widens the stop; relisted tickers, funding-interval changes, step-wise trailing and liquidation-before-stop handled per T-33 and T-35–T-38; margin-bound sizing produces no false rejection (T-41); funding estimates use evidence available by the decision time and record any default as an explicit research scenario (T-42).
- Feasibility report reproducibly enumerates executable candidates, rejection reasons, cost/stop-distance sensitivities and provenance limitations; no unsupported claim that 50 USDT is sufficient.

**Exit decision:** M1 can pass without profitable strategy or a minimum executable-order rate, but **not** without the feasibility report (SC-12) or with unresolved simulated equity/event-order accounting (SPEC SC-02). Data-quality and mark-price-proxy limitations are recorded and promotion constraints identified.

## 5. M2 — Strategy Research (primary decision gate)

**Scope**
- Volatility Breakout baseline **without pyramiding**: candidate 15m/1h, ADX(14), prior Donchian(20), EMA(200), prior-volume filter, ATR-based exit. Run as a **versioned OFFLINE research candidate**; promotion approval is not required to investigate it.
- Freeze precise signal timing, indexing, warm-up, stop-level computation and the trigger/halt evaluation cadence in SPEC (SC-04). Simulator fill/stop/funding processing is fixed by M1 (SPEC §4.5.1) and not redefined here.
- Evaluate Major + Dynamic Top Movers universe against no-trade, seeded random-entry and filter-ablation baselines with calendar-block resampling (SPEC §11); isolate incremental value of ranking and filters.
- Chronological development/validation, walk-forward, untouched holdout.
- Cost-aware 50 USDT execution feasibility, regime sensitivity, tail-risk and parameter robustness.
- **Minimal simulated Ignition:** 50 USDT start, 100 USDT observation target, fixed 30 USDT halt, research-only sizing/exposure, costs; compute using the **M1 accepted equity ledger and snapshot-trigger rules**. Estimate target-before-halt and conditional recovery feasibility; no real/simulated wallet transfer workflow is required here. Evaluate trigger/halt on `cycle_equity` with the halt-headroom cap (SPEC §7.3.1/§8.1) and report the recovery shortfall rate.

**Required sequence**
1. Publish versioned research protocol and numeric acceptance thresholds **before opening holdout**, including recovery reserve scenarios (0 USDT baseline plus at least one cost-model-error scenario) and the primary scenario. Also preregister every **exact** reserve value intended for operation as a promotion candidate under the multiple-comparison rules; otherwise a different M3 value needs a fresh holdout (SPEC §7.3.1).
2. Develop and test only on designated development/validation periods.
3. Freeze code, parameters, universe rules, cost scenarios and comparator definitions.
4. Run holdout once under preregistered rules; retain results even if unfavorable.
5. Produce concise `PASS`, `FAIL`, or `INCONCLUSIVE` evidence and decision.

**Deliverables:** reproducible research command, evidence report with config/dataset hashes, uncertainty estimates, verified/reconstructed coverage, 50 USDT feasibility and failure reasons, recovery shortfall and dead-zone rates per reserve scenario, and recovery probability within the lifetime limits.

**Acceptance:** meets all preregistered numeric gates, survives specified stress cases, has credible net-of-cost edge on executable 50 USDT sizing, and is not dependent on unsupported historical assumptions. No threshold may be invented after seeing holdout.

**Exit decision:** `PASS` allows M3 promotion planning, **not** live trading. `FAIL` may terminate the experiment. `INCONCLUSIVE` requires new evidence or a separately declared research cycle; never relabel a failed holdout as development data without explicit new-cycle governance.

## 6. M3 — Capital & Risk Engine

**Prerequisite:** jurisdiction/USD-M Futures access `VERIFIED` (§3 deadlines).

**Scope**
- Full capital phases: `IGNITION`, `RECOVERY_PENDING`, `GROWTH`, `VAULT`.
- Orthogonal operational states: `RUNNING`, `PAUSED`, `RECOVERING`, `HALTED`.
- Central risk authorization: approved config, stop-risk budget, margin/liquidation buffer, minimum order, aggregate/correlated exposure and no-trade rejection reasons.
- Fixed Ignition initial-capital loss halt at 30 USDT (not peak drawdown); research/approve additional preventive limits separately.
- Recovery simulation: observed equity >=100 triggers freeze/settlement; 50 USDT transfer only if settled funds allow 50 USDT retained **plus additional reserve**; durable idempotent intent and ledger. The exact operational reserve must pass the preregistered M2 criteria before use (SPEC §7.3.1).
- Candidate Growth/Vault high-water mark halts and Vault profit locking remain research-pending until separately approved.
- Capital-cycle closure, new-cycle authorization and lifetime limits (SPEC §7.1.1); Futures→Spot-only transfer adapter with separate key (SPEC §10).
- **Exchange-account reconciliation contract (SPEC SC-06):** map exchange mark-based unrealized PnL, wallet/realized balances, available/transferable funds, snapshot freshness, remaining costs and recovery reserve. Simulate/mock these exchange responses and failure modes in M3; actual exchange adapter stays gated to M5. Do not reopen M1's simulated accounting rules.
- Restart-safe order/position/stop reconciliation and fault injection.
- Recovery workflow substates with atomic entry freeze, durable intent/attempt records, authoritative restart reconciliation and explicit ambiguous-result handling (SPEC §7.4 / SC-14).
- Mock protective-order compatibility matrix and emergency flatten contract for position modes, partial fills, rejected/unknown stops and crash-between-fill-and-protection (SPEC §9.2 / SC-13). Exchange behavior is validated later, in M5 (SPEC §3.1).

**Deliverables:** tested capital state machine, risk engine, recovery simulator, transfer ledger, operator halt/resume controls, recovery fault-injection suite and mock protective-order compatibility evidence.

**Acceptance tests**
- 50→80→45 does not trigger the fixed 30 USDT halt; 30 or lower does.
- Observed 100 but settled 98 remains `RECOVERY_PENDING` with no transfer.
- Shortfall enters `SHORTFALL_HOLD` with only the SPEC §7.4.1 resolutions; external inflows never trigger recovery; entries beyond halt headroom are rejected (T-27–T-30); resume is refused while `cycle_equity >= 100`, including the dead zone (T-43).
- Cycle closure and lifetime limits, and transfer-direction restriction pass T-31 and T-32; reconciliation covers both regular and conditional/algo order families against mocks (T-40).
- Insufficient reserve or uncertain transfer status blocks transition.
- The evidence identifies the exact operational reserve and shows that it passed the preregistered M2 criteria (SPEC §7.3.1).
- Lost acknowledgment/restart never causes blind duplicate transfer.
- No strategy can bypass risk engine or clear a latched halt.
- Recovery success is confirmed exactly once in simulated ledger and permanently disables automatic re-Ignition.
- Fault injection at recovery workflow boundaries, lost/duplicate responses, partial flatten, stale balances, ambiguous transfer history and crash/restart never yields a duplicate transfer or premature Growth.
- Unconfirmed protective stop after fill blocks new entries and invokes bounded emergency handling; no mock compatibility assumption is silently treated as live approval.

**Exit decision:** safety contracts resolved and fault tests pass. M3 contains **no real transfer capability**.

## 7. M4 — Paper Pilot

**Prerequisite:** dedicated account `VERIFIED`. If PAPER uses a read-only key, verify its permissions and the account settings. With public data alone, record the settings as configuration assumptions until M5 (SPEC §7.6).

**Scope**
- Public live market data, simulated exchange fills and costs, persistent position/order/stop lifecycle. Runs as **mainnet shadow mode**: live mainnet data, optional read-only account key, orders built exactly as live and recorded but never sent (SPEC §3.1).
- Shared strategy/risk logic with offline mode; paper adapter with zero real-money permissions. A **paper-pilot-approved strategy/config digest** is required even if an unapproved candidate was allowed in OFFLINE research.
- Monitoring, stale-feed detection, restart recovery, alerts and emergency handling.
- Full simulated Capital Recovery rehearsal and operator procedures.
- Predeclared paper observation duration, operational SLAs and incident limits.

**Deliverables:** paper runbook, incident logs, reconciliation dashboard/CLI summaries, paper evidence report.

**Acceptance:** observation window completed; no unapproved exposure; restart/partial-fill/stop-rejection/disconnect/transfer-timeout scenarios pass; paper/live logic divergence risks documented; zero order requests and zero real transfers; PAPER starts on public data alone and refuses any key with trading, transfer or withdrawal permissions (T-34); account preconditions verified with the read-only key where configured.

**Exit decision:** `PASS` only establishes operational readiness for **considering** M5, not profitability or live approval.

## 8. M5 — Optional Controlled Live Pilot

**Prerequisites:** M2 research `PASS`, M3 safety `PASS`, M4 paper `PASS`, **separate live strategy/config authorization** (paper approval is insufficient), explicit manual authorization, all applicable account eligibility items verified at M5 start (§3), current Binance endpoint and position-mode verification, emergency runbook.

**Scope**
- Separate live adapter, minimal API permissions, exchange-hosted protective orders, isolated margin verification, live reconciliation.
- **Staged enablement** (SPEC §3.1), each stage with restart and explicit reauthorization: (1) `LIVE_SHADOW`: live account and trading key, orders sent only to the exchange test-order endpoint, simulated fills, predeclared observation window; (2) protective-order smoke test with minimal real orders under explicit approval; (3) `LIVE_ARMED`.
- Confirm transfer endpoint/permissions, exact available/transferable balance fields and post-transfer reserve.
- Validate and sign off the **current account-specific Binance conditional-order compatibility matrix** (one-way/hedge mode, supported stop endpoints/flags, protection confirmation, partial fills and emergency flatten) before live arming; unsupported protection is a hard NO-GO.
- Conservative approved sizing; staged enablement and immediate operator kill switch.
- Small controlled launch with no assumption that 50 USDT can place a compliant order.

**Acceptance:** `LIVE_SHADOW` window completed and protective-order smoke test passed (T-39), with conditional/algo-order reconciliation verified (T-40) and no protective-order type used without its own current smoke-test sign-off (SPEC INV-12); account-specific protective-order compatibility and emergency behavior explicitly signed off; no unapproved orders, protective exits confirmed, balances/positions/transfers reconciled, safety latches function, all live parameters explicitly approved. Any critical inconsistency => halt new entries and invoke emergency response.

**Go/no-go:** Operator may cancel M5 even after all technical gates. No automated deployment from research results.

## 9. Cross-milestone quality requirements

| Track | Minimum standard |
|---|---|
| Reproducibility | Versioned code/config/data, deterministic clock/seed and evidence hashes |
| Data integrity | Point-in-time availability and documented provenance; no silent reconstruction |
| Research integrity | Pre-registered holdout gates; benchmark, costs, uncertainty and multiple-comparison controls |
| Risk | No unauthorized order; sizing feasible at 50 USDT; halt and exposure tests |
| Exchange safety | Unknown orders/stops/transfers block entries; reconcile before retry |
| Security | No secrets in repo/CI/logs; live capability unavailable in M0–M4 |
| Observability | Traceable signal→risk→order→fill→ledger chain and actionable alerts |

## 10. Dependency and risk register

| Risk / dependency | Earliest affected gate | Mitigation / decision |
|---|---|---|
| Account/jurisdiction ineligible for USD-M Futures or transfers | M0 | Eligibility pre-check; `NOT_ELIGIBLE` ⇒ `BLOCKED` before M1; `UNVERIFIED` items verified by the staged deadlines (§3) |
| Repeated manual refunding after losses | M5 | PRD §5.2.1 lifetime limits enforced from the ledger |
| Historical listing/filter/spread coverage incomplete | M1/M2 | Provenance tiers, exclusion, sensitivity bounds, `INCONCLUSIVE` |
| 50 USDT cannot meet compliant stop-risk sizing | M1/M2 | Mandatory feasibility report, rejection taxonomy and sensitivity; reject symbols; may terminate project; never round up risk |
| Strategy net expectancy not positive after costs | M2 | No-trade / stop research cycle; no forced promotion |
| Ignition ruin probability too high | M2 | Predeclared rejection criterion, stress scenarios |
| Recovery 100 observation settles below eligibility | M3 | Pending + freeze; no transfer or automatic retrading |
| API/stop behavior incompatible with design | M3/M5 | Mock emergency behavior in M3/M4; validate in M5 via `LIVE_SHADOW` and the protective-order smoke test; no-go if unsafe |
| VPS/restart/network failures | M3/M4 | Durable intents, reconciliation, fail-closed, alerts |
| Scope creep into multiple strategies/governance | All | Baseline first; P1/P2 features only after evidence |

## 11. Scope priority and deferrals

- **P0:** Data integrity, PIT universe, deterministic simulation, single breakout strategy research, risk authorization, state machine, recovery simulation, safety, paper pilot.
- **P1 (conditional):** Growth/Vault pyramiding and Vault profit locking, only after separate incremental validation.
- **P2 (future):** Trend Continuation, Momentum Expansion, adaptive selector; comparator and multiplicity protocol mandatory.
- **Not required:** UI, multi-exchange, autonomous retraining, standalone governance document packs.

## 12. Working agreement and progress tracker

Use **Define → Implement → Test → Review → Fix → Merge** per milestone. Keep implementation prompts/issues small, attach acceptance tests, and update only relevant SPEC/ROADMAP sections. A single short evidence artifact per milestone is sufficient unless an incident demands more.

| Milestone | Status | Owner | Evidence | Decision |
|---|---|---|---|---|
| M0 | IN PROGRESS | Operator | [M0 verification](evidence/m0/verification.md) | Review fixes passed hosted CI at `6010a75`. Python digest pin and schema v2 live capture verified locally. Operator acceptance pending. |
| M1 | NOT STARTED | TBD | — | Pending |
| M2 | NOT STARTED | TBD | — | Pending |
| M3 | NOT STARTED | TBD | — | Pending |
| M4 | NOT STARTED | TBD | — | Pending |
| M5 | OPTIONAL / NOT AUTHORIZED | TBD | — | Pending |

### Immediate next actions

1. **PRD v1.4 is Approved & Locked** as the product baseline; no further PRD approval task remains.
2. Review SPEC open contracts **SC-01/SC-02** and M0 scope; ensure M1 simulator accounting/event priority is resolved before M1 exit; do not pre-approve research-only risk settings.
3. `README.md` created (pre-M0 version). Implement M0 with OFFLINE-only safety tests, CI, the account eligibility pre-check and forward capture, and add the README quickstart.
4. Begin M1 data provenance and exchange-feasibility research before investing in live execution.
5. Pre-register M2 numeric promotion criteria **before** holdout evaluation.

**Roadmap acceptance:** This document plans work; it does not authorize live orders, real transfers, or a production deployment.

## 13. v0.4 targeted change notes

- M0 foundation implemented on a topic branch. CI uses `ubuntu-24.04` and `windows-2025`, and README covers Windows native and WSL2 workflows.
- M0 review fixes: stricter import contract (no bare `acgs` imports, no wall-clock reads in decision layers), persistent HTTP 403/451 latch, compressed capture payloads (ledger schema v2), fixed-width UTC timestamps, verified smoke replay, Docker `check` service, and CI actions/images pinned by SHA or digest.
- M0 requires identical lint/type/test results on Ubuntu 24.04 and Windows, with Docker canonical and a two-OS CI matrix. M2 preregisters exact operational reserve candidates.
- Clarified the optional read-only key in M4, decision-time funding evidence in M1, and validation of the exact operational reserve against the M2 criteria.
- Account eligibility: `UNVERIFIED` does not block M0 or OFFLINE M1/M2; `NOT_ELIGIBLE` blocks M1. Staged deadlines are in §3. HTTP 451/403 triggers operator review.
- Funding evidence and `premiumIndex` forward capture (M0/M1, T-42); M1/M2 simulation-semantics boundary; reserve scenarios preregistered before the M2 holdout; resume refused at or above the trigger (T-43).
- Editorial: product renamed from "ACGS V2" to **ACGS** (PRD v1.4). No scope changed.
- Basis updated to `SPEC.md` v0.4.
- M2 evaluates trigger/halt on `cycle_equity` with the halt-headroom cap and reports the recovery shortfall rate.
- M3 acceptance adds shortfall hold, external-inflow and halt-headroom tests (T-27–T-30).
- Next actions: pre-M0 `README.md` created; quickstart added in M0.
- Aligned with PRD v1.4: M0 adds account eligibility pre-check, forward capture and import contracts; no testnet: M4 mainnet shadow mode, M5 staged `LIVE_SHADOW` → smoke test → `LIVE_ARMED`; M3 adds cycle closure/lifetime limits and Futures→Spot-only transfer; delivery rule 7 (stopping after M2/M4 is success).
- M3 adds hardened `RESUME_IGNITION` preconditions and conditional/algo-order reconciliation (T-40); M5 acceptance requires per-order-type smoke-test sign-off (INV-12).
- M1 adds instrument epochs, funding intervals, mark-kline coverage, Growth-level feasibility and T-33/T-35–T-38; M2 adds random-entry/ablation baselines and lifetime-limit success probability.

## Appendix — v0.3 targeted change notes

- M1 now requires a reproducible 50 USDT execution-feasibility report and explicit rejection taxonomy (SC-12), without inventing an M1 pass-rate target.
- M3/M4 mock stop-protection failures; M5 requires account-specific Binance protective-order compatibility sign-off (SC-13).
- M3 recovery requires durable workflow substates and crash/timeout/duplicate-transfer fault-injection acceptance (SC-14).
- Preserves PRD v1.3 baseline, M0–M4 prohibition on real orders/transfers, and all previously agreed milestone decisions.

### v0.3 editorial corrections (2026-11-17)

- Basis reference corrected to `SPEC.md` v0.3 (SC-12–SC-14 originate there).

## Appendix — v0.2 targeted change notes

- M1 now explicitly owns simulated equity/wallet/mark/funding accounting and regression-tested event order, while M3 owns exchange-account and transfer reconciliation.
- Clarified `OFFLINE` candidate experimentation versus `PAPER`/`LIVE` mode-specific strategy approval; no automatic promotion.
- Expanded M1 acceptance with funding/entry/exit/stop/gap ordering and accounting consistency tests.
- Updated PRD v1.3 status to **Approved & Locked**; all milestones remain unstarted pending implementation.
