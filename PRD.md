# ACGS V2 — Product Requirements Document (PRD)

**Product:** Adaptive Capital Growth System (ACGS V2)  
**Version:** 1.4 — Approved & Locked  
**Date:** 2026-11-17  
**Status:** Approved & Locked as the product baseline (2026-11-17); strategy and risk parameters remain unvalidated and research-pending  
**Target exchange:** Binance USD-M Futures  
**Initial trading capital:** 50 USDT

---

## 1. Executive Summary

ACGS V2 is an automated, research-driven futures trading system designed to experiment with aggressive growth of a small initial account, recover the original capital upon reaching a defined target, and then transition irreversibly to a more conservative compounding regime. It combines the prior ACGS emphasis on reproducible research, point-in-time data, validation, and controlled execution with a phase-based capital policy and capital-recovery workflow.

The first target is **100 USDT observed eligible Futures equity**, starting from **50 USDT**. Reaching this threshold initiates a **recovery attempt**, not an unconditional transfer. The bot must block new exposure, safely settle open positions, reconcile realized balances and transferable funds, and transfer **50 USDT** to Spot **only if the post-transfer active Futures reserve requirement is satisfied**. The target is aspirational, not guaranteed. Futures trading may result in complete loss of the stake, including losses exceeding modeled protective stops.

The initial research candidate for eventual live eligibility is **Volatility Breakout with trend/volume filtering**. It must earn live eligibility through out-of-sample research, stress testing, execution simulation, and paper trading. There is no requirement to trade if evidence is insufficient.

## 2. Problem Statement

Small futures accounts face several linked problems:

- Fixed trading sizes can violate minimum-notional, precision, and margin constraints.
- Aggressive leverage and concentrated risk can exhaust the account before a strategy's edge is established.
- Attractive backtest returns may disappear after funding, fees, slippage, survivorship bias, and changing market regimes.
- Exchange/API faults, partial fills, missing protective stops, and process restarts can turn a strategy defect into uncontrolled exposure.
- Systems often lack a reliable mechanism for recovering initial capital and preventing subsequent high-risk re-entry.

ACGS V2 addresses these through a validated strategy pipeline, explicit capital-state transitions, risk-first order authorization, exchange reconciliation, and a verifiable capital ledger.

## 3. Product Vision and Goals

### 3.1 Primary goal

Build a reliable automated system that **attempts** to grow 50 USDT to 100 USDT under an explicitly bounded Ignition policy, then recovers 50 USDT of original capital and compounds only the remaining active trading capital.

The primary deliverable is reproducible research and safe operating infrastructure, plus evidence on whether a 50 USDT strategy is viable. Stopping after M2 (no credible edge) or after M4 (paper pilot only) is a legitimate, successful outcome (§12.4).

### 3.2 Secondary goals

1. Preserve ACGS research rigor while simplifying the runtime to a Python modular monolith.
2. Support deterministic replay and comparable research, paper, and live decision logic.
3. Make risk exposure, state transitions, transfers, and failures auditable.
4. Reduce risk after capital recovery; never automatically return to Ignition.
5. Support future multi-strategy selection only after its incremental value is validated.
6. Research a point-in-time **Major + Dynamic Top Movers** universe without assuming today's winners or surviving listings were tradable in the past.

### 3.3 Non-goals

- Guaranteed doubling, fixed monthly returns, or guaranteed maximum realized loss.
- Martingale, loss-averaging grid systems, or automatic deposits to rescue losing positions.
- Autonomous live strategy discovery/retraining without independent approval.
- Multi-exchange support, high-frequency trading, social trading, or a consumer-facing UI in MVP.
- Production launch directly from an unvalidated strategy specification.

## 4. Intended User and Operating Context

**Primary user:** a single technically capable operator who understands futures risk, accepts that the 50 USDT initial stake may be lost, and wants reproducible evidence and operational controls rather than manual discretionary trading.

**Operating model:** a single account, Binance USD-M Futures, Docker-hosted Python service on a VPS; CLI/config and operational alerts are sufficient for MVP. Live trading is disabled by default and requires explicit authorization.

## 5. Capital Lifecycle and State Policy

### 5.1 Capital states

| State | Entry condition | Objective | Exit condition |
|---|---|---|---|
| `IGNITION` | New authorized capital cycle funded with 50 USDT | Attempt growth to 100 USDT | Observed eligible equity reaches 100 USDT -> `RECOVERY_PENDING`; fixed-loss halt -> operational `HALTED`, then `CYCLE_CLOSED` (§5.2.1); other risk breach -> operational `HALTED` |
| `RECOVERY_PENDING` | Recovery trigger observed | Freeze entries; settle exposure, verify transferable balance and reserve, attempt one 50 USDT Spot transfer | Verified transfer, sufficient remaining reserve and reconciled ledger -> `GROWTH`; otherwise remain pending with entries blocked until the operator explicitly resumes Ignition in the same cycle or closes the cycle (§5.3) |
| `GROWTH` | Confirmed recovery of initial 50 USDT | Lower-risk compounding of remaining active capital | Active Futures equity reaches 500 USDT -> `VAULT`; breach -> pause/halt |
| `VAULT` | Active Futures equity reaches 500 USDT | Preserve capital and selectively lock realized profits | Continues until manually disabled or halted |
| `CYCLE_CLOSED` (terminal cycle status) | Fixed-loss halt after flattening and reconciliation, or explicit operator closure from `RECOVERY_PENDING` | No trading; preserve the cycle record and residual funds | None; a new cycle only under §5.2.1 |

**Important:** Growth starts with **at least 50 USDT active Futures capital** after the one-time 50 USDT recovery transfer. The baseline reserve requirement is 50 USDT **after transfer and reconciliation**, subject to stricter operational reserves if research warrants. If settled funds do not meet both requirements, do not transfer or transition to Growth automatically. The Vault threshold refers to active Futures equity, not combined Spot + Futures balances. Capital phase and operational state are separate: a `HALTED` operation never silently changes the capital phase.

### 5.2 Ignition stop policy

- Initial reference equity: **50 USDT** per authorized capital cycle; the reference is fixed throughout Ignition and is not reset when equity makes a new high.
- **Fixed loss-from-initial-capital limit:** 40% of the 50 USDT initial reference, equivalent to a **20 USDT loss** and a **30 USDT equity halt trigger**. This is **not** a 40% maximum drawdown measured from the running equity high-water mark.
- **Halt trigger:** observed eligible Futures equity at or below **30 USDT** (or a more conservative pre-emptive risk rule). If equity rises to 80 USDT and then falls to 45 USDT, the fixed 30 USDT halt has **not** fired, even though the peak-to-trough drawdown exceeds 40%; independent drawdown/exposure controls may halt sooner.
- On trigger: block new entries, initiate approved protective/flattening procedures, record the event, and require manual authorization to restart a new capital cycle. The cycle then closes under §5.2.1.
- This is an **operational trigger, not a guaranteed 30 USDT floor**: gaps, liquidation, slippage, fees, and API faults can lead to worse outcomes.
- No automatic top-up or restart of Ignition. State transitions are sticky after successful recovery.
- The fixed 20 USDT initial-capital loss limit is a **maximum operational boundary to test**, **not** a per-trade risk allowance or a guaranteed realized-loss cap. Separate, potentially stricter peak-drawdown, per-trade and portfolio preventive limits remain research-pending.

### 5.2.1 Capital-cycle closure and lifetime limits

- A capital cycle **closes** (`CYCLE_CLOSED`) when the fixed-loss halt fires and all exposure has been flattened and reconciled, or when the operator explicitly closes the cycle from `RECOVERY_PENDING`. Other operational halts (stale data, reconciliation faults, etc.) do not close the cycle.
- Residual funds of a closed cycle are not trading capital. ACGS leaves them untouched until the operator withdraws them or applies them to a new cycle.
- A new cycle requires **all** of the following:
  - explicit operator authorization;
  - a written post-mortem of the closed cycle covering cause, realized execution versus the cost/slippage model, and rule compliance;
  - confirmation that live approval remains valid. An execution-model deviation beyond the preregistered tolerance suspends live approval and returns the strategy to research;
  - funding of exactly 50 USDT. The residual counts toward it; any top-up is recorded as newly committed capital, never as profit. Any excess above 50 USDT must first be moved out of the bot's Futures wallet. A residual excess of at most 0.50 USDT that cannot practically be removed is tolerated, but it is excluded from cycle equity and is never trading capital, never part of the risk budget and never counted toward the recovery trigger.
- Top-ups are permitted **only between cycles**, never during an open cycle and never toward recovery eligibility.
- **Lifetime limits:** at most **two** Ignition cycles in total, and at most **100 USDT** cumulative committed capital (the initial 50 USDT plus all top-ups). When either limit would be exceeded, no new cycle may start and the live experiment ends. Raising either limit requires a PRD revision, not a runtime or configuration override.
- Rationale: repeated manual refunding after losses would otherwise act as a manual martingale, contrary to §3.3.

### 5.3 Capital recovery transaction

**Observation, execution eligibility, and completion are different events.** An observed eligible Futures equity reading >= 100 USDT (from a fresh exchange snapshot using a defined mark-to-market policy) triggers `RECOVERY_PENDING`. It is **not** proof that 100 USDT will remain after closing positions, fees, funding, slippage, and settlement.

**Recovery threshold contract:**
- `recovery_observation_threshold = 100 USDT` (the agreed trigger to freeze entries and begin settlement/reconciliation).
- `recovery_transfer_amount = 50 USDT` and `minimum_post_transfer_active_equity = 50 USDT`.
- `additional_operational_reserve >= 0 USDT` is a separately approved, non-double-counted buffer determined in `SPEC.md` before any live transfer.
- The **minimum settled-equity execution threshold** is `100 USDT + additional_operational_reserve`, assuming no further settlement/transfer costs; actual execution additionally requires transferable funds and all safety checks below. Any expected remaining costs must be incorporated into the pre-transfer eligibility calculation. This threshold is **not** a replacement for the 100 USDT observation trigger.
- If the observed 100 USDT trigger is reached but settled funds are insufficient, the system remains in `RECOVERY_PENDING` with new entries blocked; it must not silently resume trading to chase the additional reserve.

1. Persist the recovery intent and atomically **block new entries**. Use a unique recovery-cycle identifier.
2. Cancel/resolve open orders; safely close/settle all positions; verify no residual exposure or contingent orders that can recreate positions.
3. Reconcile realized PnL, fees, funding, wallet balance, transferable balance, and transfer history from fresh exchange data.
4. Compute **recoverable surplus** based on settled, transferable Futures funds, not unrealized profit. Before any transfer, require all of the following:
   - `transferable_funds >= 50 USDT`;
   - after transferring 50 USDT and accounting for remaining costs, **active Futures equity >= 50 USDT + additional_operational_reserve**;
   - no unresolved exposure, transfer attempt, or reconciliation mismatch.
5. If any condition fails (for example, the initial 100 USDT observed equity becomes 98 USDT after settlement), **do not transfer, do not count recovery as completed, and do not return automatically to Ignition**. Remain `RECOVERY_PENDING`, block new entries, alert the operator, and require an explicit recovery/resume decision. The permitted decisions are: re-check without trading; explicitly resume Ignition in the same cycle with the unchanged 50 USDT reference and 30 USDT halt, subject to automated cost-model, risk-configuration and headroom checks and permitted for at most two shortfalls per cycle (SPEC §7.4.1); or close the cycle (§5.2.1). External top-ups never count toward recovery eligibility.
6. If all conditions pass, transfer **exactly 50 USDT** Futures -> Spot using an explicitly authorized exchange capability. Verify via transfer history and both wallet balances; persist the exchange transfer identifier and update the protected-capital ledger exactly once.
7. Enter `GROWTH` only after confirmed transfer, verified post-transfer reserve, and ledger reconciliation. No automatic re-entry into Ignition is permitted after successful recovery.

On timeout or ambiguous acknowledgment, **reconcile the previous attempt before retrying**; never retry blindly. A transfer failure is not success, and a 100 USDT balance reading is not a transfer authorization on its own. Exact exchange balance fields, reserve calculation (including avoidance of double-counting costs), and equity sampling cadence are to be defined in `SPEC.md` and tested with fault injection before live operation.

**Protected capital** means removed from the bot's Futures trading exposure; it remains exposed to exchange/custody risk while on Spot. ACGS never initiates a Spot-to-Futures transfer. After a confirmed recovery, the operator should move protected funds out of the bot account's reach (for example to a separate account) following the runbook.

### 5.4 Growth and Vault drawdown policies (provisional)

- Growth candidate halt threshold: **12% drawdown from phase high-water mark**.
- Vault candidate halt threshold: **8% drawdown from phase high-water mark**.
- These are proposed risk settings, subject to research and acceptance; they are not yet approved live parameters.
- Vault profit-locking candidate: transfer **40% of eligible realized profit** upon a defined doubling of post-vault reference equity, provided exposure and reserve requirements permit it. Exact trigger accounting and transfer cadence remain to be specified and validated.

### 5.5 Operational states (orthogonal to capital states)

`RUNNING`, `PAUSED`, `RECOVERING`, and `HALTED`. An operational halt blocks new entries in any capital phase. Restart requires explicit operator authorization and successful reconciliation. No module may silently override a halt.

## 6. Strategy and Research Requirements

### 6.1 Point-in-time coin universe: Major + Dynamic Top Movers

The engine must evaluate a **dynamic eligible universe**, not a permanent list of today's winners. Candidate construction follows these steps:

1. **Discovery:** identify Binance USD-M **USDT perpetual** instruments that were tradable at the decision time, including instruments subsequently delisted. Preserve historical listing/delisting evidence and flag any reconstructed or uncertain dates.
2. **Liquidity eligibility:** use only completed historical candles to rank by trailing **30-day average quote volume**; **top 30** is an initial research setting, not a proven optimum. Exclude pegged assets/index products where appropriate. Use actual spread/order-book observations when available; until then, disclose the missing-spread limitation and stress slippage rather than claiming real liquidity costs are known.
3. **Capital feasibility:** check time-appropriate exchange minimum notional, `minQty`, `stepSize`, tick size, available collateral, fee/funding reserve, and the position quantity implied by the stop-distance risk budget at **50 USDT**. If a permitted order cannot be sized, **skip the symbol; never round up beyond the approved risk budget**.
4. **History quality:** require enough *contiguous* OHLCV/funding history for indicators and research warm-up (candidate **60–90 days**, or longer where the actual indicator requires it). Reject/flag missing data and historical filter estimates; do not invent observations.
5. **Directional momentum ranking:** compute candidate **14-day signed return** using data available at the decision timestamp. For momentum-following strategies, positive momentum is long-eligible and negative momentum short-eligible; **absolute negative performance must not be interpreted as a long signal**. Other rankings may be researched separately and predeclared.
6. **Selection and allocation:** majors such as BTCUSDT and ETHUSDT are included in discovery but **are not exempt from feasibility rules**. A liquid high-momentum altcoin may replace an ineligible major. Rank and select candidates using fixed, research-defined rules; the risk engine may still reject all entries.

**Timing contract:** daily universe/rank snapshots for day D use only information fully available before the decision time (e.g., candles completed through day D−1 for a ranking made at the start of D). Recompute membership/ranks on an explicit cadence (initial candidate: daily) without retroactive changes. Never use eventual survivors, realized future top movers, future exchange rules, or future funding to choose the past portfolio.

**Historical data provenance and missingness policy:** Each material point-in-time input (listing/delisting dates, instrument filters, liquidity observations, spreads and funding) must be tagged `VERIFIED` (supported by timestamped primary evidence), `RECONSTRUCTED` (derived from documented assumptions/proxies), or `UNAVAILABLE` (no defensible observation). Store source, observation/as-of time, method, coverage and confidence limitations. Never silently promote reconstructed values to verified status or fill unavailable observations with future information.

**Two research evidence tiers:**
- **Exploratory research:** May use explicitly identified `RECONSTRUCTED` inputs when assumptions are documented, causal, and tested under conservative alternative scenarios. Such results may generate or eliminate hypotheses but **cannot alone authorize promotion**.
- **Promotion-grade research:** Requires credible point-in-time support for every critical trade-eligibility and execution assumption. `RECONSTRUCTED` inputs are acceptable **only if** their method and affected coverage were declared before evaluation and sensitivity/bounds testing demonstrates that the decision is robust to plausible reconstruction error. Report verified-only and reconstruction-dependent results separately where feasible. If critical data are `UNAVAILABLE`, exclude the affected symbol-period; if uncertainty prevents a defensible conclusion or materially changes a pass/fail outcome, mark the result **INCONCLUSIVE**, not PASS. Do not impose a blanket verified-only requirement on all exploratory history, and do not count unsupported reconstructed evidence as verified.

The exact liquidity, minimum-history, momentum, concentration, and spread/cost thresholds are **research candidates**, not live defaults. **No eligible symbol ⇒ no trade.**

### 6.2 MVP strategy candidate: Volatility Breakout

- Timeframes under evaluation: **15m and 1h**.
- Regime candidate: ADX(14) > 25.
- Volume candidate: previous completed candle volume > 1.5x the preceding 20-candle average (precise, non-overlapping indexing to be fixed in `SPEC.md`).
- Long candidate: close above **prior completed** Donchian(20) upper band and above EMA(200), for a long-eligible instrument.
- Short candidate: close below **prior completed** Donchian(20) lower band and below EMA(200), for a short-eligible instrument.
- Candidate exit: ATR(14)-based trailing stop at 2.5x ATR from favorable price extreme.
- All indicators must use only data available at the decision timestamp; no same-candle lookahead.
- Entry, exit, stop updates, and order timing must have explicit candle-close/intrabar semantics before testing.
- Test the breakout **without pyramiding first**; measure its incremental expectancy after all trading costs rather than assuming leverage creates an edge.

These values are **research hypotheses**, not claims of positive expectancy. Parameter variations and any decision to promote a strategy must be made before inspecting the relevant untouched holdout.

### 6.3 Additional research candidates

- Trend Continuation.
- Momentum Expansion.
- Adaptive strategy selection: research-only until multiple candidates independently pass validation and a predeclared selection mechanism demonstrates incremental out-of-sample value.

**Adaptive-selector comparison gate:** Before evaluating an adaptive selector on untouched holdout, predeclare its selection features, retraining/selection cadence, transaction-cost assumptions, and comparator protocol. Compare against (a) the best single strategy chosen **using development data only**, with that choice frozen before holdout, and (b) simple predeclared baselines, including no-trade where appropriate. Report incremental net-of-cost performance and uncertainty, accounting for strategy-selection multiplicity. A selector is not promoted solely because it beats an ex-post best strategy or an inadequately specified benchmark.

Do not implement or optimize additional candidates merely because the initial breakout fails. A failed research gate may end the experiment or return it to a **new explicitly separated development cycle**; it must never lead to retroactive holdout tuning.

### 6.4 Pyramiding

- **Disabled in Ignition.**
- May be enabled in Growth/Vault only if separately validated **against the same strategy without pyramiding**.
- Proposed candidate trigger: favorable movement of 1.5x ATR from the last entry; maximum three layers.
- **Do not size adds directly as 50% of unrealized PnL.** Every add must fit a combined position/portfolio loss budget, account for fees and liquidation distance, and maintain exchange-native protective orders in live mode.
- Break-even adjustments must consider total blended position, fees, funding, slippage, and order constraints; they do not guarantee zero realized loss.
- Model partial fills, margin usage, stop failure, and sudden adverse reversals when evaluating the incremental benefit.

### 6.5 Research quality gates

- Point-in-time eligibility and delisting-aware universe where historical evidence permits; identify periods relying on reconstructed filters.
- Correct timestamps, contiguous data, and no survivorship, lookahead, or cross-validation leakage.
- Fee, funding, slippage, spread uncertainty, minimum order, margin, and realistic fill assumptions.
- Chronological development/validation windows; walk-forward evaluation; **untouched holdout**, never tuned after opening.
- Benchmark against no-trade and simple alternatives; report incremental value from ranking, filters, and pyramiding separately.
- Parameter sensitivity, regime analysis, trade distribution, drawdown, and tail-risk stress testing.
- Monte Carlo/bootstrap estimates of probability of reaching 100 USDT **before** an Ignition halt at 30 USDT; report uncertainty and assumptions, and do not imply a dependable probability from inadequate data. During **M2**, this uses a **minimal simulated Ignition policy** (50 USDT initial equity, 100 USDT observation trigger, fixed 30 USDT halt, selected research-only sizing/exposure caps and realistic execution costs). The M2 model estimates trigger, loss, and conditional recovery feasibility; it **does not perform transfers or claim complete M3 state-machine/reconciliation behavior**.
- Evaluate both **signal behavior on a sufficiently sized reference account** (if needed) and **actual executability on 50 USDT**. A profitable reference backtest does not grant micro-capital eligibility. Report whether results depend on reconstructed historical inputs, and distinguish exploratory from promotion-grade evidence.
- **Pre-registered numeric promotion gates:** Before opening the relevant holdout, record measurable acceptance thresholds in `SPEC.md` or a versioned research protocol referenced by `SPEC.md`. At minimum define the effective trade sample/observation window, net expectancy and uncertainty criterion, maximum tolerable drawdown and ruin probability, cost/slippage stress scenarios, and the 50 USDT execution-feasibility pass rate. Include decision rules for inconclusive results and multiple comparisons. Threshold values must be justified from research constraints and risk tolerance, **not chosen after seeing holdout outcomes**.
- Evidence-based approval or rejection. A candidate can fail; **no-trade** is valid.

## 7. Functional Requirements

| ID | Requirement | Priority | Acceptance intent |
|---|---|---|---|
| FR-01 | Ingest historical and live OHLCV, funding, symbol filters, and liquidity metadata | P0 | Reproducible datasets and integrity checks |
| FR-02 | Maintain point-in-time Major + Dynamic Top Movers universe, liquidity/momentum ranks and 50 USDT execution eligibility | P0 | Historical membership/ranking causally reconstructable; unaffordable orders skipped |
| FR-03 | Run deterministic backtests with execution costs | P0 | Same inputs/config yield same results |
| FR-04 | Produce strategy evidence reports and immutable configuration versions | P0 | Every promoted strategy traceable to evidence |
| FR-05 | Generate strategy signals without direct exchange permissions | P0 | Signal layer cannot submit orders |
| FR-06 | Authorize orders through centralized capital/risk engine | P0 | Unsafe or unaffordable orders rejected |
| FR-07 | Support Ignition/Growth/Vault and operational state machine | P0 | Transitions tested including restart/failure |
| FR-08 | Enforce Ignition 30 USDT halt trigger; distinguish recovery trigger from successful recovery | P0 | No automatic re-Ignition/refill; no transfer if post-settlement reserves fail |
| FR-09 | Execute and reconcile orders, positions, fills, and protective stops | P0 | Partial fills and ambiguous responses handled |
| FR-10 | Support idempotent 50 USDT recovery transfer to Spot after settled-funds and post-transfer reserve checks | P0 | Confirmed once, no duplicate transfer, >=50 USDT active reserve on entry to Growth |
| FR-11 | Provide paper trading and historical replay using shared decision logic | P0 | Behavior traceable across modes |
| FR-12 | Record equity, exposure, fees, funding, and capital ledger | P0 | Auditable balances and phase accounting |
| FR-13 | Provide kill switch, halt-on-uncertainty, alerts, and manual resume | P0 | Fail-closed on critical inconsistencies |
| FR-14 | Support optional, risk-budgeted pyramiding | P1 | No add violates combined risk limits |
| FR-15 | Support Vault profit locking | P1 | Only eligible realized profit transferred |
| FR-16 | Support validated adaptive strategy selection | P2 | No live selection without independent evidence |

## 8. Risk and Execution Safety Requirements

1. **Risk sizing:** define risk as modeled loss to the protective stop, including conservative execution costs and stress fills; never equate risk per trade with leverage or allocated margin. Stops are not guaranteed loss limits.
2. **Risk/trade candidates — RESEARCH ONLY:** Ignition **8–12%**, Growth **1–2%**, Vault **0.5–1%** are scenarios to evaluate, **not approved live defaults**. In particular, 8–12% Ignition risk is highly aggressive. The system starts with **no live risk authorization**; any final cap must be justified by cost-aware research, tail-risk stress tests, 50 USDT feasibility, paper evidence, and explicit operator approval, and may be materially lower.
3. **Leverage ceilings — RESEARCH ONLY:** Ignition 10x, Growth 5x, Vault 3x are proposed ceilings, not targets or automatic phase settings. Final allowed leverage depends on margin, maintenance tiers, stop-to-liquidation distance, available funds, and phase risk budget.
4. **Margin:** use isolated margin by default; verify actual exchange setting and prevent accidental cross-margin exposure.
5. **Exchange filters:** query actual minimum notional, step size, tick size, quantity limits, and precision; do not rely on a hardcoded universal 5 USDT floor.
6. **Protection:** every filled position must have an accepted, exchange-hosted protective exit. On stop-placement failure, block further exposure and invoke a predefined emergency flattening path.
7. **Lifecycle:** handle submitted, acknowledged, partially filled, filled, canceled, expired, rejected, and unknown orders; use unique client order IDs where supported.
8. **Recovery:** after restart or connection loss, reconcile exchange orders, positions, balances, and transfers before allowing entries.
9. **Limits:** enforce max aggregate exposure, max concurrent positions, correlated exposure, available collateral, funding/fee reserve, liquidation buffers, and maximum combined stop-loss exposure across all active positions and pyramiding layers.
10. **Fail-closed:** stale market data, inconsistent positions, missing stop protection, or uncertain transfer status must suspend new entries.
11. **Permissions:** separate paper/live environments; least-privilege API keys, no withdrawal permissions, and only explicitly needed internal transfer permissions where supported. Paper mode uses at most a read-only key. Trading and transfer use separate keys where the exchange supports it, and the transfer capability is restricted in code to Futures-to-Spot only.
12. **Exchange compatibility:** validate current Binance Futures conditional-order endpoints, supported flags, trigger semantics, and regional/account restrictions during implementation. Do not assume `STOP_MARKET` + `reduceOnly` works identically in all modes/endpoints.

## 9. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Reliability | Restart-safe state, durable transitions, reconciled order and transfer lifecycle |
| Security | Secrets never committed or logged; restricted API permissions; auditable manual actions |
| Determinism | Versioned datasets, configs, strategy code, and random seeds for research |
| Observability | Structured logs, metrics, alerts for halt, stale data, order failures, and transfer events |
| Maintainability | Typed Python modules, documented interfaces, CI, unit/integration/property/replay tests |
| Performance | Timely candle-close decisions and protective-order handling; latency targets measured in paper mode |
| Deployment | Single VPS via Docker Compose; minimal dependencies and safe restart behavior |
| Traceability | Each live order links to signal, approved strategy version, risk decision, and exchange execution |

## 10. Proposed Architecture

**Architecture pattern:** Python modular monolith with clearly separated components and adapters.

```text
Historical / Public Market Data + Exchange Metadata
                    |
        Data Integrity + PIT Eligible Universe
                    |
        Research / Deterministic Backtest
                    |
       Evidence + Explicit Strategy Approval
                    |
             Strategy Signal Logic
                    |
           Capital Phase + Risk Authorization
                    |
         Simulated Execution Adapter (M1–M3)
                    |
         Paper Execution Adapter (M4)
                    |
    Optional Live Exchange Adapter (M5, gated)
                    |
      Exchange State / Order Reconciliation
                    |
     Capital Recovery / Vault Transfer Ledger

Cross-cutting: Persistent State, Structured Logs, Alerts, Kill Switch
```

**Architecture constraint:** Strategies never receive exchange order permissions. The same strategy and risk decisions must be comparable across replay/paper/live, but adapters and external side effects are separately guarded. Research and simulated execution must function without account credentials. Exchange order submission and wallet transfer capabilities belong only to explicitly authorized late-stage adapters.

**Technology baseline:** Python 3.12+, CCXT with native Binance API where required, NumPy/Pandas, SQLite for a controlled single-writer MVP, pytest, Docker Compose, and GitHub Actions. PostgreSQL is an option if operational concurrency warrants it. Historical replay, paper trading, and live modes must share core strategy and risk logic but use separate adapters.

## 11. Milestones and Release Gates

Implementation order is intentionally **research-first and risk-before-order**. Milestones denote working software and evidence, not standalone governance projects.

| Milestone | Scope | Exit evidence |
|---|---|---|
| M0 — Foundation | Minimal repository, Docker, config, CI, local SQLite and test harness; OFFLINE default and secret safety; account eligibility pre-check; forward capture of public exchange metadata | Reproducible setup, automated tests, secret scan; account eligibility recorded (ineligible => `BLOCKED`) |
| M1 — Market & Offline Execution | Historical ingestion, data integrity, PIT universe, exchange filter snapshots, deterministic offline broker/backtester with fee/funding/slippage/order constraints; **no live order adapter** | Real-source provenance, replay correctness, gap/lookahead tests, cost and small-order rejection tests |
| M2 — Strategy Research | Volatility Breakout hypothesis without pyramiding; dynamic universe/selection, comparative net-of-cost results, chronological walk-forward/holdout, tail-risk and 50 USDT feasibility; **minimal simulated Ignition risk/target/halt policy** for research only | Reproducible research with explicit pass/fail/no-trade decision; 50-to-100-before-30 and conditional recovery-feasibility estimates; forward holdout remains untouched until preregistered |
| M3 — Capital & Risk Engine | Full Ignition/Recovery/Growth/Vault state machine, production-oriented risk authorization, aggregate exposure/margin safety, halt, **recovery transfer simulator and ledger**, optional pyramiding research only after baseline validation | State-transition and restart tests; settlement/reserve calculations; duplicate-transfer and fault-injection tests; no external transfer |
| M4 — Paper Pilot | Mainnet shadow mode: persistent paper trading on live mainnet data with optional read-only account access, orders recorded but never sent, simulated fills, order/position lifecycle, reconciled state, alerts and recovery rehearsal | Demonstrated stability and safety over a predeclared observation window; no real exchange orders |
| M5 — Optional Controlled Live Pilot | Separate Binance live adapter and internal transfer capability with current API compatibility checks, exchange-hosted protective exits, explicit authorization and emergency procedure | Prior gates passed, account and regional eligibility checked, live-shadow stage and protective-order smoke test passed, reconciliation/fault tests, explicit manual launch approval |

**Release rules:** M1 may pass without a profitable strategy; M2 may legitimately conclude **no edge**. No strategy is auto-promoted to M4/M5. A failed research or operational gate blocks deployment; M5 may be canceled if no validated edge exists or 50 USDT cannot safely satisfy exchange constraints. No real exchange order or real internal transfer may be executed during M0–M4; simulated fills and simulated transfers are permitted in offline/paper modes. No exchange testnet is used. M4 paper trading runs as a **mainnet shadow mode**: live mainnet data and, optionally, a read-only account key; orders are fully built, validated locally and recorded, but never sent. M5 begins with a **live shadow** stage in which orders go only to the exchange test-order endpoint, followed by a minimal real-order protective-order smoke test, before armed live trading.

## 12. Success Metrics and Decision Criteria

### 12.1 Research success

- Positive out-of-sample net expectancy with credible uncertainty estimates **on executable 50 USDT sizing**, not merely a larger reference account.
- Drawdown, risk of ruin, and probability of reaching recovery target measured under realistic costs.
- Robustness across plausible fees/slippage, parameter perturbations, and market regimes.
- Sufficient effective trade sample and observation window, with numeric thresholds, uncertainty methods and pass/fail/inconclusive rules preregistered before holdout evaluation.

### 12.2 Operational success

- Zero unapproved live orders and zero automatic state reversions.
- All open exposure reconciled and protected, or system halts and invokes documented emergency handling.
- Capital recovery transfer recorded exactly once and confirmed independently.
- Restart, disconnection, partial fill, rejected stop, and transfer-timeout scenarios pass acceptance tests.

### 12.3 Product outcome metrics

- Time and estimated probability to reach the 100 USDT trigger **and successfully recover 50 USDT while retaining the required Futures reserve**, before halt (reported with uncertainty, not as commitments).
- Post-recovery active-equity return and maximum drawdown.
- Total capital recovered to Spot and total net equity across wallets, reported separately.
- Trading cost drag and uptime/reconciliation incidents.

### 12.4 Interpretation of outcomes

- Under aggressive Ignition sizing, a single cycle resolves in roughly 15 trades, so its outcome says little about whether an edge exists. Live success or failure is **not** validation; M2 research evidence remains the only basis for an edge claim.
- Illustration only, not a forecast: with fixed 10% risk, a 35% win rate, 1R losses and no costs, a simple simulation gives P(100 before 30) ≈ 29% at zero expectancy and ≈ 56% at +0.225R per trade, with a median of 14–17 trades per cycle.
- The expected monetary outcome is small relative to the engineering effort. The main value is the research and operating infrastructure and the evidence it produces. Ending after M2 (no edge) or after M4 (paper only) is a successful outcome.

## 13. Dependencies, Risks, and Assumptions

**Dependencies:** Binance API availability and account eligibility; exchange precision/minimum order requirements; sufficient quality historical data; reliable VPS connectivity; correct handling of fees, funding, and liquidation.

**Key risks:** full capital loss, inaccurate stop-loss assumptions, liquidation before stops execute, small-account order infeasibility, overfitting, missing delisted assets, execution divergence, duplicate transfer, exchange custody risk, and regulatory or account restrictions.

**Assumptions to verify:** account permissions for Futures-to-Spot transfer; supported protective-order API behavior and current regional restrictions; symbol-level minimum order requirements; historical universe completeness; availability of credible spread/liquidity observations; minimum feasible stop-based risk size at 50 USDT; ability to retain at least 50 USDT after the one-time recovery transfer.

## 14. Open Decisions and Research-Pending Items

| ID | Question | Owner / resolution path |
|---|---|---|
| OD-01 | Exact Ignition per-trade risk and leverage caps | Research + stress testing |
| OD-02 | Provisional trigger equity definition, pricing basis, freshness, sampling cadence and additional operational reserve (including remaining costs) | SPEC contract before recovery simulation |
| OD-03 | Exact transferable-balance check, mandatory 50 USDT post-transfer reserve and any additional fees/funding reserve | Capital policy test before live |
| OD-04 | Growth/Vault high-water mark reset and halt behavior | Risk policy specification |
| OD-05 | Exact Vault profit-lock reference, cadence, and minimum transfer | Capital ledger design |
| OD-06 | Signal candle timing, Donchian indexing, volume baseline, and stop semantics | Strategy specification |
| OD-07 | Pyramiding combined risk budget and layer sizing | Research + risk validation |
| OD-08 | Numeric OOS promotion thresholds, effective sample size, uncertainty, ruin/drawdown limits, stress scenarios, execution feasibility, minimal M2 simulated Ignition policy, and paper observation window | Pre-registration before holdout |
| OD-09 | Validate top-30/30-day liquidity and 14-day signed momentum thresholds, concentration, spread/funding eligibility, cadence, and the declared exploratory/promotion-grade rules for reconstructed-data coverage | M2 market/data research |
| OD-10 | Whether 50 USDT supports safe, compliant minimum orders for target symbols | Exchange feasibility test |
| OD-11 | Live transfer permissions and operational custody safeguards | Exchange/API verification |
| OD-12 | Predeclared adaptive-selector comparator, multiplicity correction and incremental-value threshold | Research protocol before selector holdout |
| OD-13 | Capital-cycle closure, top-up rules and lifetime limits | **Resolved in v1.4** (§5.2.1) |

## 15. Lightweight Documentation and Source of Truth

ACGS V2 maintains four authoritative core documents: `README.md`, `PRD.md`, `SPEC.md`, and `ROADMAP.md`. Milestone-specific research artifacts, configurations, evidence reports, and operational runbooks are permitted when necessary, but do not constitute additional governance documents or independent sources of product truth.

| File | Responsibility |
|---|---|
| `README.md` | Project overview, setup, commands and operating modes |
| `PRD.md` | Product intent, scope, outcomes and acceptance requirements (this document) |
| `SPEC.md` | Executable behavior, strategy timing, risk formulas, exchange contracts and state invariants |
| `ROADMAP.md` | Milestone scope, sequencing, implementation status and minimal acceptance checklist |

Do **not** create mandatory standalone CANON, ADR, Build Plan, audit packs or governance reports. Record material architecture tradeoffs briefly in `SPEC.md` when needed; ordinary decisions can live alongside code and tests. Workflow: **Define → Implement → Test → Review → Fix → Merge**. Focus on working, tested deliverables and avoid repeated documentation-only review cycles.

**Decision status legend:**
- **Agreed baseline:** initial 50 USDT, Ignition observed-equity trigger 100 USDT, **fixed 40% loss from initial reference equity** halt trigger (30 USDT; not peak-based drawdown or a guaranteed floor), **conditional** one-time 50 USDT Capital Recovery with >=50 USDT remaining active reserve, no automatic re-Ignition, research-first live approval, capital-cycle closure with at most **two** Ignition cycles and **100 USDT** lifetime committed capital (§5.2.1).
- **Proposed design:** 30-day top-30 liquidity and 14-day directional momentum ranking, Growth/Vault drawdown thresholds, leverage ceilings, **research-only** per-trade risk ranges, Vault profit locking, modular-monolith technology choices.
- **Research pending:** strategy profitability, exact parameters, exchange feasibility, and release thresholds.

---

**PRD sign-off status:** Approved & Locked (2026-11-17). This approval authorizes design and **offline engineering/research planning only**, not live orders, real transfers, or production trading.


## Appendix A — Change Log

### v1.4 — Approved & Locked (2026-11-17)

- Added §5.2.1: capital-cycle closure (`CYCLE_CLOSED`), new-cycle conditions, between-cycle-only top-ups, and lifetime limits of two cycles / 100 USDT committed capital (OD-13).
- §5.3: enumerated the permitted operator decisions after a recovery shortfall; ACGS never transfers Spot to Futures; protected funds should leave the bot account's reach.
- §8: separate trading and transfer keys; transfer restricted in code to Futures-to-Spot.
- Resume after a recovery shortfall requires automated checks and is limited to two shortfalls per cycle; an opening excess of at most 0.50 USDT is excluded from cycle equity.
- §11: account eligibility pre-check and forward data capture in M0; no testnet. M4 is a mainnet shadow mode (read-only key, orders recorded, never sent); M5 starts with a live shadow stage via the exchange test-order endpoint and a minimal real-order smoke test.
- §3.1 and §12.4: research and infrastructure are the primary deliverable; a single live cycle is not validation; stopping after M2 or M4 is a successful outcome.
- Unchanged: 50 USDT start, 100 USDT trigger, 30 USDT fixed-loss halt, 50 USDT recovery with >= 50 USDT retained, no automatic re-Ignition.

### v1.3 — Approved & Locked

- Editorial update (2026-11-17): status changed from final review candidate to **Approved & Locked**; §15 reworded to define four *authoritative core* documents while permitting milestone research artifacts, configurations, evidence reports and runbooks. No product baseline changed.

- Clarified Ignition's **fixed 40% loss from initial equity (50 → 30 USDT)** versus a distinct high-water-mark drawdown measure; no change to the 30 USDT halt threshold.
- Allowed a **minimal simulated Ignition risk/target/halt policy in M2** for research without prematurely implementing the full M3 capital lifecycle or real transfers.
- Defined **exploratory versus promotion-grade** historical evidence; reconstructed data may support controlled exploration, while promotion requires robust, disclosed point-in-time assumptions and may conclude inconclusive.
- Preserved all other product decisions and the four-document operating model.

### v1.2

- Clarified 100 USDT observation trigger versus reserve-aware settled-funds transfer eligibility, without changing the agreed Capital Recovery amount.
- Added explicit historical input provenance states and rules for missing/reconstructed data in promotion-grade evidence.
- Added preregistered comparator requirements for adaptive strategy selection.
- Added numeric, preregistered research promotion gates before opening holdout.
- Preserved the four-document governance model and all previously agreed capital-state policies.
