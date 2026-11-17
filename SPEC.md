# ACGS V2 — System Specification (SPEC)

**Version:** 0.3 — Pre-Implementation Safety Gates  
**Date:** 2026-11-17  
**Status:** Engineering baseline candidate; offline design and research only  
**Source of truth for product intent:** `PRD.md` v1.3 (**Approved & Locked**)  
**Target:** Binance USD-M USDT perpetuals; Python modular monolith; initial capital 50 USDT

> **Authority:** This SPEC operationalizes PRD v1.3. It does not approve a trading strategy, risk parameters, live orders, or transfers. Items marked **OPEN** require a versioned decision and test before the relevant gate. Numeric candidates are research-only unless expressly identified as agreed product baselines.

## 1. Scope and system boundaries

- **M0–M4:** offline research, deterministic simulation, and paper execution. No real exchange order or internal transfer.
- **M5 (optional):** isolated, explicitly authorized live adapter after all gates; no implicit promotion.
- **Out of scope for MVP:** multi-exchange, consumer UI, autonomous retraining, HFT, martingale, automatic account rescue deposits.
- **Runtime:** one logical trading writer; independent research jobs may run against immutable snapshots. Secrets are never required for offline replay.

### 1.1 Design invariants

| ID | Invariant |
|---|---|
| INV-01 | Only the execution adapter can submit orders; only the transfer adapter can move funds. Strategy code has neither capability. |
| INV-02 | Strategy authorization is **mode-specific**: `OFFLINE` may simulate a versioned, unapproved research candidate; `PAPER` requires explicit paper-pilot approval; `LIVE` requires separate live approval. Every mode enforces its applicable simulated/real risk gates, valid state, fresh data and provenance. No research result automatically promotes a strategy. |
| INV-03 | An unresolved order, position, stop, balance, or transfer discrepancy blocks new exposure. |
| INV-04 | Capital phase cannot silently move backward; successful recovery permanently prevents automatic re-Ignition. |
| INV-05 | No transfer is marked complete without independently reconciled exchange evidence and durable ledger state. |
| INV-06 | Historical decision at time *t* uses only observations available by *t*. |
| INV-07 | Minimum-size rounding never increases modeled risk above the authorized budget. |
| INV-08 | `OFFLINE` is the default mode; M0–M4 cannot instantiate a live order or transfer adapter. |
| INV-09 | A filled live position requires exchange-accepted protective exit coverage; failure invokes an emergency protocol and blocks entries. |
| INV-10 | A halt cannot be cleared by a strategy, restart, config reload, or reconnect. |

## 2. Logical architecture

```text
Historical/public feeds --> Data ingestion --> PIT dataset + provenance
                                              |
                                Universe + feature engine
                                              |
                          Strategy / deterministic research
                                              |
                    Versioned signal + config ID (mode-gated)
                                              |
                          Capital policy + risk authorization
                                              |
                 +----------------------------+--------------------+
                 |                            |                    |
          Offline broker               Paper adapter        Live adapter (M5)
                 |                            |                    |
                 +----------------------------+--------------------+
                                              |
                              Order/position reconciliation
                                              |
                         Capital state + transfer ledger (M3+)

Cross-cutting: durable state, audit events, metrics, alerts, kill switch.
```

**Modules:** `config`, `clock`, `market_data`, `provenance`, `universe`, `features`, `strategy`, `research`, `broker_sim`, `capital`, `risk`, `execution`, `reconciliation`, `transfers`, `ledger`, `ops`. Names are organizational suggestions, not mandated file paths.

**Ports:** market data, exchange metadata, order execution, account snapshots, transfers, clock, persistence and notifications. Implementations must be replaceable without changing strategy/risk logic.

## 3. Operating modes and authorization

| Mode | Data | Fills | Strategy authorization | Orders/transfers | Gate |
|---|---|---|---|---|---|
| `OFFLINE` | Historical immutable snapshots | Deterministic simulated | Versioned research candidate **permitted without promotion approval** | No real side effects | Default; M1/M2 |
| `PAPER` | Public live feeds | Simulated, cost-aware | Explicit approval for **paper pilot** tied to strategy/config digest | No real side effects | M4 |
| `LIVE` | Exchange feeds/account | Exchange-confirmed | **Separate** explicit live approval and operator arming; paper approval is insufficient | Explicitly authorized only | M5 |

- All modes retain appropriate risk checks. `OFFLINE` experimentation may simulate rejected or extreme risk hypotheses under labeled test configurations; it does **not** grant live-risk authorization. Research candidates must have versioned configuration, inputs and results.
- Approval scope is tied to **mode + strategy/config digest**. Promotion between `OFFLINE`, `PAPER` and `LIVE` is never automatic and may be withdrawn. A candidate that fails research gates cannot gain paper/live approval merely by changing execution mode.
- A mode is fixed at process startup; switching requires shutdown and explicit reauthorization.
- Live requires separately provisioned credentials, approved live strategy/config digest, environment/account checks, and a manual arming procedure. **OPEN:** precise authorization mechanism, expiry, and dual confirmation.
- An operator may pause/stop entries at any time. Recovery from `HALTED` requires reconciliation, an audit event, and explicit operator approval; approval does not imply automatic phase reset.

## 4. Canonical time, data and provenance

### 4.1 Time semantics

- Store timestamps in UTC with exchange event time, observation/as-of time, ingestion time, and dataset version where applicable.
- Candle `[open_time, close_time)` is eligible only after close and after the configured availability delay. A decision at *t* cannot use incomplete candles or a later snapshot.
- Universe selection for day D uses information available before its decision boundary (candidate: through completed day D−1). Cadence is research-pending.
- Replays must run on an injected deterministic clock, not system wall time.

### 4.2 Data contracts

| Contract | Required fields (minimum) | Validation |
|---|---|---|
| `InstrumentSnapshot` | symbol, contract type, listing/delisting validity, trading status, filters, effective/as-of times, source/provenance | Time-appropriate USDT perpetual eligibility |
| `Candle` | symbol, interval, open/close times, OHLCV, quote volume, source, availability time | Unique key, contiguous series, sane prices/volumes |
| `FundingObservation` | symbol, funding rate, event/availability times, source | Never apply future funding in past decisions |
| `LiquidityObservation` | trailing quote-volume window, spread/order-book sample if present, as-of time | Complete lookback and known uncertainty |
| `UniverseSnapshot` | decision time, eligible symbols, exclusion reasons, rank inputs, policy/config digest | Deterministically reproducible membership |
| `SimulatedAccountSnapshot` (M1) | wallet/realized balance, realized PnL, unrealized PnL, fee/funding postings, margin usage, simulated equity, mark source, event/snapshot time, run digest | Deterministic cash/equity reconciliation; no exchange-transfer claims |
| `ExchangeAccountSnapshot` (M3 contract; M5 implementation) | exchange wallet, mark-based unrealized PnL, margin, available/transferable balance, equity, observation timestamp, source/account ID | Freshness and exchange-account reconciliation; transfer eligibility distinct from equity |
| `EvidenceRecord` | data/code/config hashes, windows, costs, results, uncertainty, provenance tier, gate decision | Reproducible promotion evidence |

### 4.3 Provenance policy

Each critical historical input is classified:

- `VERIFIED`: supported by timestamped primary evidence.
- `RECONSTRUCTED`: causally estimated using a documented proxy/method and coverage bounds.
- `UNAVAILABLE`: no defensible observation.

Persist source, method, as-of time, coverage and limitations. Exploratory research may use documented reconstructions; promotion-grade research must test sensitivity to reconstruction error, separate verified-only results where feasible, and mark **INCONCLUSIVE** if critical uncertainty changes the pass/fail decision. Exclude symbol-periods with critical `UNAVAILABLE` inputs. Never use future information to fill gaps.

### 4.4 Data failure behavior

Missing bars, stale feeds, uncertain exchange filters, invalid timestamps, or insufficient warm-up invalidate the affected signal. Critical uncertainty yields **no trade**. Backfills are versioned, never silently overwrite research evidence. A position with missing valuation data must not be silently marked at a stale price: fail the affected replay or use an explicitly specified and stress-tested conservative policy.

### 4.5 Simulated equity accounting contract (M1 exit requirement)

M1 must implement and test the **simulation accounting contract** before M1 is accepted; M2 uses this identical accounting for equity-trigger, halt, expectancy, and recovery-feasibility research. This is separate from **M3 exchange account and transfer reconciliation**.

- **Position and cash basis:** signed quantity `q` is positive for long and negative for short. Track executed fill prices, position cost basis, realized trading PnL, trade fees, funding cashflows and any other modeled cash adjustments as distinct immutable ledger events. Entry/exit commissions are charged on fills; a closed position realizes trading PnL exactly once. An open position's unrealized PnL is `Σ[q × (mark_price − cost_basis_price)]` under the declared linear USDT-perpetual contract convention.
- **Canonical simulated equity:** `sim_wallet = initial_wallet + cumulative_realized_trading_PnL + cumulative_funding_cashflow − cumulative_fees + other_explicit_cash_adjustments`; `sim_equity = sim_wallet + unrealized_PnL`. Posted funding and fees must not be charged again at trade exit. Margin reservation reduces *available collateral* but is **not** subtracted a second time from equity. Realized PnL and mark-to-market PnL must not overlap.
- **Mark price and source:** prefer timestamped historical exchange mark-price observations when available and causally valid. If unavailable, M1 may use an explicitly labeled deterministic `CANDLE_CLOSE_PROXY` for close-of-bar equity snapshots and a corresponding disclosed bar-open proxy for open-event valuation; proxies are **not** claimed to be Binance mark prices. Execution prices (including adverse slippage) remain distinct from valuation marks. The proxy limitation and margin/liquidation sensitivity must be disclosed in M2 evidence; a required mark missing at an open position must not silently reuse an old mark.
- **Event ordering:** resolve an explicit, deterministic timestamp/priority ordering for exchange settlement, pending order fills, protective stops, funding postings and snapshots. Apply each event once to the position held at the actual modeled settlement time. When intrabar order/funding/stop chronology cannot be reconstructed from OHLC, enforce a documented conservative rule and flag the ambiguity; never imply that OHLC provides unseen tick ordering. The chosen rule and tied-timestamp behavior must be frozen and regression-tested before M1 exit.
- **Snapshot cadence and trigger semantics:** emit reconciled simulated snapshots after each modeled fill/funding/stop event and at each completed bar close, with timestamp, mark source, position state and wallet/equity breakdown; record which snapshots are eligible for M2's 100 USDT observation and 30 USDT halt checks. Do **not** infer a trigger merely from a candle's favorable high/low when no eligible event-time equity is observable. If the model restricts trigger evaluation cadence, declare it before M2 research and measure sensitivity; do not retroactively change it after observing outcomes.
- **Accounting acceptance:** for each event/snapshot, cash ledger totals reconcile to `sim_wallet`, and `sim_equity − sim_wallet == unrealized_PnL` within a predeclared numerical tolerance. Verify flat-position equity equals wallet, realized/unrealized transition on partial and complete closes, funding charged to the correct holder, fee/slippage accounting, and deterministic replay with adverse gaps. **OPEN before M1 acceptance:** exact event priority at ties, source/proxy policy, rounding/tolerances and valuation timestamp rules (SC-02).

## 5. Point-in-time universe and execution feasibility

The universe pipeline at each decision time:

1. Discover historically tradable Binance USD-M **USDT perpetual** instruments, including later-delisted symbols when evidence supports them.
2. Apply status, history-quality, liquidity and exclusion rules using only prior completed observations.
3. Candidate liquidity ranking: trailing 30-day mean quote volume, top 30 (**research-only**).
4. Compute candidate 14-day **signed** return; positive momentum may support long eligibility, negative momentum short eligibility. Do not turn absolute negative returns into long signals.
5. Test symbol-specific filters, order quantity, stop distance, margin, fee/funding reserve, and liquidation buffer at **50 USDT**. A major such as BTC/ETH has no exemption.
6. Persist selected symbols, ranks, exclusion reasons, input provenance and config hash. No eligible symbol means no trade.

**Sizing feasibility:** Let `risk_budget` be approved maximum modeled loss, `stop_distance` be the absolute entry-to-stop price distance, and `cost_buffer(q)` conservative costs and stressed fill loss. Find an exchange-compliant quantity `q` such that `q × stop_distance + cost_buffer(q) <= risk_budget`, while satisfying margin, liquidation distance and symbol filters. Quantize down to allowed steps; if minimum order cannot satisfy all constraints, reject. Exact formulas and maintenance-margin tiers are **OPEN** pending exchange compatibility and research.

### 5.1 Micro-capital execution eligibility contract (M1 hard gate)

For every candidate entry, compute and persist a deterministic `EligibilityDecision` containing symbol, decision/as-of time, filter provenance, side, entry/stop assumptions, candidate risk budget, requested and quantized quantity, notional, initial/maintenance margin assumptions, leverage, estimated fee/funding reserve, stressed stop loss, liquidation buffer, result and reason codes. Use exchange-specific effective filters; do not hardcode a universal minimum notional. Quantity may be rounded **down** to a valid step; never round up to satisfy minimum notional if doing so violates risk. Reject any order that fails **any** constraint. Leverage can reduce initial margin but cannot relax the stop-loss risk budget.

**Deterministic rejection taxonomy:** `MISSING_PIT_FILTER`, `UNVERIFIED_CRITICAL_FILTER`, `STALE_METADATA`, `INSUFFICIENT_HISTORY`, `MIN_NOTIONAL`, `MIN_QTY`, `STEP_SIZE`, `PRICE_TICK`, `RISK_BUDGET`, `INSUFFICIENT_COLLATERAL`, `FEE_FUNDING_RESERVE`, `LIQUIDATION_BUFFER`, `SPREAD_COST_UNCERTAIN`, `UNKNOWN_CONSTRAINT`. Multiple reasons may be reported; critical unknowns fail closed. Filter uncertainty and reconstruction-dependent eligibility must be explicitly separated from verified cases.

**M1 mandatory evidence:** Produce a versioned **50 USDT Execution Feasibility Report** covering the candidate point-in-time universe and research windows: attempted candidates, executable count and rate, rejection counts by reason/symbol/regime, size/notional/margin distributions, and cost/stop-distance sensitivities. Distinguish *technical eligibility* from *strategy profitability*; report coverage and uncertainty rather than treating reconstructed filters as verified. M1 acceptance requires the report and correct rejection behavior, **not a preselected minimum pass rate**. A numeric promotion-grade feasibility threshold is preregistered before the M2 holdout (SC-03). If no feasible orders exist under defensible assumptions, record a no-trade/feasibility blocker rather than weakening risk controls.

## 6. Strategy contract — MVP candidate only

**Strategy:** Volatility Breakout with trend/volume filtering; no claim of edge.

| Parameter | Candidate | Status |
|---|---|---|
| Bar interval | 15m and 1h | Research |
| Trend strength | ADX(14) > 25 | Research |
| Volume | Previous completed bar > 1.5 × preceding 20-bar mean | Research |
| Breakout | Close beyond **prior completed** Donchian(20) band | Research |
| Direction | Long above EMA(200), short below EMA(200) | Research |
| Exit | ATR(14) trailing stop, candidate 2.5 × ATR | Research |
| Pyramiding | Disabled in Ignition and baseline research | Agreed baseline |

- Signal engine returns `NO_SIGNAL`, `LONG_CANDIDATE`, or `SHORT_CANDIDATE` with timestamp, source bars, feature values, strategy/config digest and reason codes.
- Strategy cannot calculate final authorized order quantity or submit an order.
- **OPEN before M2:** exact candle-close decision time, next-bar execution, volume-window indexing, Donchian band exclusion of signal candle, ADX/ATR/EMA warm-up, intrabar stop ordering, stop updates, funding timing, and tie-breaking for simultaneous events.
- Evaluate baseline without pyramiding first. Additional families and adaptive selector are research-only until separately promoted.

### 6.1 Adaptive selector promotion contract (future)

Before selector holdout, freeze features, selection cadence, costs, multiple-testing method, and comparators: (a) best single strategy selected **only on development data**, frozen for holdout; (b) simple predeclared baselines including no-trade when relevant. Require incremental net-of-cost benefit with credible uncertainty. Ex-post best comparator cannot be used to claim promotion.

## 7. Capital and operational state machines

### 7.1 Capital phases

| Phase | Entry | Allowed behavior | Transition |
|---|---|---|---|
| `IGNITION` | Explicitly authorized new cycle, reference 50 USDT | Risk-authorized candidate trades, no pyramiding | Eligible observed equity >=100 -> `RECOVERY_PENDING` |
| `RECOVERY_PENDING` | Recovery intent durably recorded | **No new entries**; settle, reconcile, possibly transfer | Confirmed transfer + post-transfer reserve -> `GROWTH` |
| `GROWTH` | 50 USDT transfer confirmed; active Futures equity >=50 plus reserve | Lower-risk approved strategy | Active equity >=500 -> `VAULT` |
| `VAULT` | Verified active Futures equity >=500 | Approved preservation policy; optional validated profit locking | No automatic phase regression |

**Ignition fixed-loss policy:** Reference is fixed at 50 USDT throughout the authorized cycle. `eligible_equity <= 30 USDT` triggers an operational halt and protective/flattening procedure. This is **40% loss from initial capital**, **not** 40% drawdown from peak equity. A path 50→80→45 does not trip this fixed threshold; additional peak-drawdown controls are **OPEN**. The 30 USDT threshold is not a guaranteed loss floor.

**Growth/Vault candidate preventive halts:** 12% / 8% from phase high-water mark, respectively; **research-only**, not approved live values. Exact phase-high reset, equity basis, and treatment of external transfers are **OPEN**.

### 7.2 Orthogonal operational states

- `RUNNING`: entry may be considered only if all other gates pass.
- `PAUSED`: no new entries; continue exposure protection/reconciliation.
- `RECOVERING`: controlled restoration of exchange/account truth; no new entries.
- `HALTED`: latched safety stop; no new entries; emergency management only, manual authorization required to resume.

Safety faults (stale data, unknown orders, unprotected position, reconciliation mismatch, risk breach) latch pause/halt as specified in a versioned severity matrix (**OPEN before M3/M4**). Restart does not clear a halt.

### 7.3 Recovery transaction protocol

**Agreed baselines:** observation threshold 100 USDT, transfer amount 50 USDT, minimum post-transfer active equity 50 USDT, additional operational reserve >=0 (to be specified). These are distinct from transfer execution eligibility.

1. Persist unique `recovery_cycle_id` and `RECOVERY_PENDING` intent **before** any side effect; atomically block entries.
2. Cancel/resolve open orders and flatten/settle exposure; reconcile no residual positions or contingent re-entry orders.
3. Obtain fresh exchange evidence of realized wallet, available/transferable funds, fees, funding, pending costs and transfers.
4. Require `transferable_funds >= 50` and **post-transfer active equity >= 50 + additional_operational_reserve**, net of remaining costs. The simple settled-equity threshold is `100 + additional_operational_reserve` only if no additional costs remain.
5. If checks fail, stay `RECOVERY_PENDING`, block entries, alert operator; no automatic return to Ignition or trading to chase the shortfall.
6. In M3 simulate a unique transfer intent and acknowledgment. In M5 submit only after explicit permission; on ambiguous acknowledgment, query exchange transfer history **before any retry**.
7. Mark success only when transfer ID/history, both wallet balances, reserve, and durable ledger reconcile. Then enter `GROWTH` once.

**Exactly-once outcome** is a required logical property, not an assumption about exchange API idempotency. Maintain a durable intent/attempt ledger and refuse blind retries. **OPEN before M3:** exchange-specific equity/realized/unrealized account fields and mark-price reconciliation, exchange snapshot freshness/cadence, transferable-funds and reserve formula, transfer timeout/retry policy and crash consistency. **M1 simulated equity accounting and event timing are resolved under SC-02 before M1 acceptance, not deferred to M3.** **OPEN before M5:** supported internal-transfer endpoint/permissions and account/regional eligibility.

Protected Spot funds are excluded from futures trading capital but remain subject to exchange/custody risk; no automatic redeposit.

### 7.4 Recovery substate and crash-consistency contract (M3 hard gate)

Within `RECOVERY_PENDING`, persist one of `FREEZE_ENTRIES`, `FLATTENING`, `RECONCILING`, `TRANSFER_PENDING`, `VERIFYING`, `COMPLETED`, or `MANUAL_INTERVENTION`. These are **workflow substates**, not new capital phases. Every transition records `recovery_cycle_id`, source/target substate, timestamp, evidence digest, operator/automation actor and a monotonic version in the durable store. The entry freeze and recovery intent must commit atomically before flattening or transfer side effects. `COMPLETED` is reachable only after independent transfer-history, wallet and reserve reconciliation.

**Crash/timeout rules:** After restart, reconstruct the last durable intent and query authoritative order, position, balance and transfer evidence before taking another side effect. Unknown close-order or transfer acknowledgment must not cause blind resubmission. If transfer history cannot uniquely establish success/failure, remain frozen in `MANUAL_INTERVENTION` (or `VERIFYING` with a bounded reconciliation policy); never infer success from a local timeout or a single wallet snapshot. Reconcile contingent orders and residual exposure before transfer. Manual resolution must be audited and cannot override minimum reserve or bypass evidence requirements.

**M3 mandatory fault suite:** Inject process termination before/after each persisted transition, before/after sending close/transfer requests, lost/duplicated/out-of-order acknowledgments, partial fills, stale wallet snapshots, insufficient post-settlement reserve, ambiguous transfer history and temporary API outage. Assert no duplicate logical recovery, no new entry while pending, no transfer when eligibility is false, and no `GROWTH` without verified 50 USDT recovery plus required active reserve. The simulated transfer adapter must not contact a real exchange.

### 7.5 Vault profit locking (optional)

Candidate: lock 40% of eligible realized profit on a defined doubling of post-vault reference equity. **Not an approved rule.** Reference basis, eligible profit, fees, reserve, cadence, minimum transfer, and repeat-trigger behavior are **OPEN**. Never transfer against unrealized PnL alone.

## 8. Risk authorization

Risk is modeled adverse loss to a protective stop **plus conservative fees, slippage and stress fills**, not margin used or leverage. Exchange stops cannot guarantee the modeled loss.

| Phase | Candidate risk/trade | Candidate leverage ceiling | Authorization |
|---|---:|---:|---|
| Ignition | 8–12% | 10× | **Research-only; highly aggressive** |
| Growth | 1–2% | 5× | Research-only |
| Vault | 0.5–1% | 3× | Research-only |

**No live risk parameters are authorized.** `OFFLINE` research uses versioned candidate settings and simulated risk checks without strategy-promotion approval; `PAPER` and `LIVE` use separate mode-specific authorizations (Section 3). Live approval requires M2 evidence, 50 USDT sizing feasibility, stress tests, paper evidence and manual sign-off. Risk authorization evaluates phase, operational state, applicable mode/config, freshness, stop distance, exchange filters, margin/liquidation buffer, combined stop exposure, correlated positions, concurrency, funding/fee reserve and halt limits. Reject with auditable reason codes when any gate fails.

**M2 minimal simulated Ignition policy:** fixed 50 USDT initial reference, 100 USDT observation target, 30 USDT fixed-loss halt, candidate sizing/exposure caps and realistic costs. Estimate target-before-halt and conditional recovery feasibility; **do not** model M3 as implemented or claim a real transfer occurred.

**OPEN:** exact max exposure, correlated risk, maximum positions, preemptive peak drawdown, daily loss, stop-to-liquidation buffer, candidate parameter grid, and portfolio loss budget. Do not infer these values from the 40% account halt.

## 9. Order execution and reconciliation

### 9.1 Order lifecycle

`CREATED -> AUTHORIZED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED/FILLED` with alternative `REJECTED`, `CANCELED`, `EXPIRED`, and `UNKNOWN` branches. Actual fills and position changes come from exchange evidence; acknowledgments alone do not establish execution.

- Durable order intent, deterministic client ID where supported, side, symbol, quantity, strategy/risk authorization IDs and expected protective exit.
- Handle partial fills, duplicate messages, out-of-order events, lost acknowledgments, cancellation races, and restart.
- Unknown status => no blind resubmission; query open orders, trades/fills and positions first.
- After each fill, reconcile exchange-hosted protective exit quantity/side and enforce a predefined emergency flattening path if protection cannot be confirmed.
- Confirm actual isolated-margin setting, position mode, leverage, filters and current conditional-order API behavior. Do not assume `STOP_MARKET`/`reduceOnly` combinations are universally supported.

### 9.2 Protective-order compatibility and emergency contract (M3 mock; M5 exchange gate)

Maintain a versioned compatibility matrix for the **actual** Binance account and API configuration: one-way versus hedge position mode, supported conditional-order endpoints and trigger types, `reduceOnly`/`closePosition` applicability, quantity rules, margin/position-mode requirements, cancel/replace semantics and protective-order visibility after fills. Do not assume a specific `STOP_MARKET` parameter combination works universally. Exchange-native protective coverage must be independently confirmed from exchange state, not merely from an order-submission acknowledgment.

On a fill with missing, rejected, expired, insufficient or uncertain stop coverage: immediately block new entries, latch the configured safety state, attempt the preapproved bounded emergency flattening procedure where safe, and reconcile residual positions and contingent orders. Repeated blind submissions are forbidden; unresolved exposure requires alerting and operator intervention. Partial fills, stop-size adjustments, rejected conditional orders, reconnects and crash-between-fill-and-stop are mandatory mock/fault scenarios by M3/M4.

**M5 hard gate:** Validate the compatibility matrix and emergency behavior against current Binance endpoints in an appropriate supported test environment/account mode, then explicitly sign off the account-specific live configuration before arming. Test-environment success alone does not establish production account compatibility; unresolved restrictions or unsupported protection => **NO-GO**. No real orders are authorized by this SPEC.

### 9.3 Reconciliation

Before entries at startup/reconnect and after uncertainty, compare local intents, exchange orders/fills, positions, balances, protective exits, and transfer history. Classify mismatches as `RESOLVED`, `RETRYABLE`, or `CRITICAL`; critical mismatch latches halt and alerts. **OPEN:** precise cadence, staleness SLAs and emergency action matrix.

### 9.4 Pyramiding (P1)

Disabled in Ignition. Growth/Vault candidate: add after favorable 1.5 ATR movement, up to three layers, only if validated versus no-pyramiding baseline. Do **not** size adds as a direct fraction of unrealized profit. Each add must satisfy total-position and portfolio risk limits with blended fees, funding, stop, margin and liquidation risk.

## 10. Persistence, audit and security

- SQLite single-writer MVP; transactional updates for capital phase, operational latch, order intents, transfer intents, ledger and audit events. Research datasets/evidence are immutable and content-addressed/versioned.
- Minimal logical records: `config_versions`, `dataset_versions`, `universe_snapshots`, `research_evidence`, `capital_cycles`, `state_events`, `order_intents`, `fills`, `position_snapshots`, `risk_decisions`, `transfer_intents`, `transfer_attempts`, `capital_ledger`, `reconciliation_events`.
- Every risk decision and live order links to strategy version, data/config digest, phase, account snapshot and reason codes.
- Secrets injected at runtime; never in repo, logs, datasets or test fixtures. Restrict API permissions; no withdrawal permission; separate paper/live identity and credentials.
- Backup and restore tests must preserve safety latches and transfer-intent history. **OPEN:** retention, backup cadence, recovery point and alert delivery SLAs.

## 11. Research protocol and promotion gates

Research must use chronological development/validation windows, walk-forward tests, and an untouched holdout. Costs include fees, funding, slippage/spread uncertainty, margin/filter feasibility and realistic fills. Compare no-trade and simple baselines, parameter sensitivity, regime breakdowns, and incremental value of universe ranking, filters and pyramiding.

**Before opening a holdout**, pre-register numeric thresholds in a versioned protocol referenced by this SPEC:

1. Effective trade sample and minimum observation duration.
2. Net expectancy criterion and uncertainty method/bound.
3. Maximum acceptable drawdown and estimated ruin probability (with uncertainty).
4. Cost/slippage stress grid and survival criterion.
5. 50 USDT executable-order feasibility/pass rate.
6. Rules for multiple comparisons, reconstruction coverage and `PASS`/`FAIL`/`INCONCLUSIVE`.
7. For M2, simulated probability of 100 USDT observation **before** 30 USDT halt, and conditional feasibility of a settled 50 USDT recovery with 50 USDT retained plus reserve.

**No numeric thresholds are invented in this draft.** They are an explicit M2 prerequisite; post-hoc thresholds are prohibited. A reference-account backtest alone cannot promote a 50 USDT strategy. `INCONCLUSIVE` does not authorize live trading.

## 12. Verification matrix

| ID | Scenario | Expected result | Earliest gate |
|---|---|---|---|
| T-01 | Replay same dataset/config/seed | Identical decisions and fills | M1 |
| T-02 | Candle/metadata only available after decision time | Excluded from decision | M1 |
| T-03 | Missing critical filter or insufficient warm-up | Symbol/signal excluded with reason | M1 |
| T-04 | Minimum order exceeds stop-risk budget | Reject; never round up | M1/M3 |
| T-05 | Simulated fees/funding/slippage adverse | Cost-aware result; no optimistic fill | M1/M2 |
| T-06 | Equity 50→80→45 | No fixed 30 USDT halt; other preventive rules may apply | M2/M3 |
| T-07 | Eligible equity <=30 | Halt latched, entries blocked | M2/M3 |
| T-08 | Observed 100, settled 98 | Pending; no transfer, no Growth | M3 |
| T-09 | Settled 100 + reserve, 50 transferable | Single transfer intent; Growth only after confirmed balance/ledger | M3 simulation |
| T-10 | Transfer acknowledgment lost | Reconcile before retry; no duplicate transfer | M3/M5 |
| T-11 | Restart with unknown order/position | Block entries until reconciled | M3/M4 |
| T-12 | Partial fill without confirmed protective stop | Block entries, emergency handling | M3/M4 |
| T-13 | Strategy calls order API directly | Impossible by module boundary/permissions | M0+ |
| T-14 | Critical historical data reconstructed | Sensitivity and provenance disclosed; inconclusive if material | M2 |
| T-15 | Holdout opened before protocol frozen | Gate fails | M2 |
| T-16 | Live adapter or real transfer attempted in M0–M4 | Blocked by construction and tests | M0–M4 |
| T-17 | Open, scale, partially close, fully close, fee/funding posting and equity snapshot | `sim_wallet + unrealized_PnL == sim_equity`; cash postings counted once; flat equity equals wallet | M1 |
| T-18 | Funding exactly at/near entry open, exit open and stop; same-timestamp events | Funding attributed to position actually held under frozen event priority; no double posting | M1 |
| T-19 | Gap-through-stop, intrabar stop versus funding with unknown sequence, missing candle during open position | Deterministic conservative outcome or fail-closed replay; no stale marks or false certainty | M1 |
| T-20 | Unapproved, versioned strategy runs in `OFFLINE`; same digest requested in `PAPER`/`LIVE` without relevant approval | OFFLINE simulation allowed with risk checks; PAPER/LIVE denied; no automatic mode promotion | M1/M4/M5 |
| T-21 | Historical mark absent; valuation uses declared OHLC proxy | Mark source exposed and reproducible; never labeled as verified Binance mark; M2 sensitivity disclosed | M1/M2 |
| T-22 | Minimum notional met only by rounding quantity up beyond risk budget | Reject with reason codes; leverage does not override risk | M1 |
| T-23 | Historical candidate universe includes unaffordable/uncertain orders | Versioned 50 USDT feasibility report with rejection distribution and provenance split | M1 |
| T-24 | Partial fill followed by stop rejection/unknown status | Block entries, emergency procedure and reconciliation; no unprotected silent continuation | M3/M4 |
| T-25 | Recovery crashes at each workflow boundary or transfer response is lost | Durable restart recovery, no blind duplicate transfer, no premature Growth | M3 |
| T-26 | Unsupported Binance stop flags in account position mode | M5 compatibility NO-GO; no live arming | M5 |

## 13. Open contract register

| ID | Must be resolved by | Decision |
|---|---|---|
| SC-01 | M1 | Candle availability, PIT filters, provenance and gap handling |
| SC-02 | **M1, before M1 acceptance** | Simulated equity accounting (`sim_wallet`, realized/unrealized PnL, fee/funding ledger, mark source/proxies, snapshot cadence), deterministic fill/stop/funding priority, slippage, order filters and liquidation assumptions; verify with T-17–T-19/T-21 |
| SC-03 | Before M2 holdout | Pre-registered numeric gates, sample size, confidence and multiplicity |
| SC-04 | **M2, frozen before holdout** (with SC-03) | Strategy indexing, intrabar stop semantics, universe ranking cadence |
| SC-05 | **M2, frozen before holdout** (with SC-03); M3 production engine | M2 minimal Ignition sizing and target/halt simulation |
| SC-06 | M3 | Exchange-specific account-equity/mark-price field mapping, snapshot freshness, realized/transferable balance reconciliation, costs and post-transfer reserve; **not** simulator PnL/event accounting |
| SC-07 | M3 | State transitions, peak guards, risk/exposure limits and halt recovery |
| SC-08 | M3 | Durable transfer-intent/reconciliation algorithm and ledger invariants |
| SC-09 | M4 | Paper pilot duration, alerts, fault-response and operational acceptance |
| SC-10 | M5 | Binance endpoint/permission/position-mode compatibility and live arming |
| SC-11 | Optional P1/P2 | Vault locking, pyramiding and adaptive selector promotion |
| SC-12 | M1 | Micro-capital eligibility reason codes, exchange-filter provenance, feasibility report and sensitivities (T-22/T-23) |
| SC-13 | M3 mock; M5 live | Protective-order compatibility matrix, emergency flatten safety contract, account-mode validation (T-24/T-26) |
| SC-14 | M3 | Recovery workflow substates, durable intent, retry ambiguity and fault-injection suite (T-25) |

A contract is not considered resolved merely by selecting a value: it requires rationale, versioned configuration, and tests/evidence appropriate to its gate.

## 14. Change control and release policy

- PRD controls **what and why**; SPEC controls **precise behavior and invariants**; ROADMAP controls **when and acceptance**; README controls **setup and operation**.
- Changes to capital baselines or product scope require PRD review. Technical choices may be resolved here with short rationale and test references; no mandatory separate CANON/ADR/audit pack.
- Workflow: **Define → Implement → Test → Review → Fix → Merge**.
- A failed M2 gate may stop the project. No automatic promotion, no repeated holdout tuning, and no live trading until M5 explicitly authorized.

## 15. v0.3 targeted change notes

- Added an explicit M1 50 USDT execution-feasibility contract, rejection taxonomy and required report; numeric promotion threshold remains a preregistered M2 decision.
- Added protective-order compatibility and emergency behavior as M3/M4 mock tests and an account-specific M5 hard gate.
- Added durable Recovery Pending workflow substates, crash/timeout reconciliation and mandatory M3 fault injection.
- Added T-22–T-26 and SC-12–SC-14; preserved PRD v1.3 capital baselines, M0–M4 no-live policy and four-document governance.

### v0.3 editorial corrections (2026-11-17)

- T-12 earliest gate aligned to M3/M4 (consistent with T-24 and ROADMAP M3 acceptance).
- SC-04 and SC-05 must be frozen before the M2 holdout together with SC-03, since both affect holdout results.
- No invariant, baseline or acceptance requirement otherwise changed.

## Appendix — v0.2 targeted change notes

- Moved simulated wallet/equity/mark/funding/fee and timestamp accounting acceptance into **M1 (SC-02)**; preserved exchange account/transfer reconciliation in **M3 (SC-06)**.
- Replaced blanket strategy approval with **mode-specific** research/paper/live permissions (INV-02, Section 3).
- Added M1 regression cases for funding/order/stop timing, gaps, event ties, and accounting invariants (T-17–T-21).
- Recognized **PRD v1.3 — Approved & Locked** without changing its capital or product baselines.
