# ACGS — System Specification (SPEC)

**Version:** 0.5 — M1 simulation contracts

**Date:** 2026-11-17  
**Status:** Engineering baseline candidate; offline design and research only  
**Source of truth for product intent:** `PRD.md` v1.4 (**Approved & Locked**)  
**Target:** Binance USD-M USDT perpetuals; Python modular monolith; initial capital 50 USDT

> **Authority:** This SPEC operationalizes PRD v1.4. It does not approve a trading strategy, risk parameters, live orders, or transfers. Items marked **OPEN** require a versioned decision and test before the relevant gate. Numeric candidates are research-only unless expressly identified as agreed product baselines.

## 1. Scope and system boundaries

- **M0–M4:** offline research, deterministic simulation, and paper execution. No real exchange order or internal transfer.
- **M5 (optional):** isolated, explicitly authorized live adapter after all gates; no implicit promotion.
- **Out of scope for MVP:** multi-exchange, consumer UI, autonomous retraining, HFT, martingale, automatic account rescue deposits.
- **Runtime:** one logical trading writer; independent research jobs may run against immutable snapshots. Secrets are never required for offline replay.

### 1.1 Design invariants

| ID | Invariant |
|---|---|
| INV-01 | Only the execution adapter can submit orders; only the transfer adapter can move funds. Strategy code has neither capability. Enforced per §2.1. |
| INV-02 | Strategy authorization is **mode-specific**: `OFFLINE` may simulate a versioned, unapproved research candidate; `PAPER` requires explicit paper-pilot approval; `LIVE` requires separate live approval. Every mode enforces its applicable simulated/real risk gates, valid state, fresh data and provenance. No research result automatically promotes a strategy. |
| INV-03 | An unresolved order, position, stop, balance, or transfer discrepancy blocks new exposure. |
| INV-04 | Capital phase cannot silently move backward. The only permitted backward transition is the explicit, audited operator `RESUME_IGNITION` from `RECOVERY_PENDING` after a failed (shortfall) recovery attempt (§7.4.1). Successful recovery permanently prevents automatic re-Ignition. |
| INV-05 | No transfer is marked complete without independently reconciled exchange evidence and durable ledger state. |
| INV-06 | Historical decision at time *t* uses only observations available by *t*. |
| INV-07 | Neither quantity nor price/tick rounding may increase modeled risk above the authorized budget (§5.2). |
| INV-08 | `OFFLINE` is the default mode; M0–M4 cannot instantiate an order-submitting or transfer adapter, including one that targets the exchange test-order endpoint. `PAPER` may hold only a read-only account key (§3.1). |
| INV-09 | A filled live position requires exchange-accepted protective exit coverage; failure invokes an emergency protocol and blocks entries. |
| INV-10 | A halt cannot be cleared by a strategy, restart, config reload, or reconnect. |
| INV-11 | Capital-phase triggers, halt checks and high-water marks use `cycle_equity` (§7.3.1), which excludes unapproved external inflows (§7.6) and any opening excess (§7.1.1). |
| INV-12 | A protective-order type and configuration may be used in `LIVE_ARMED` only after passing the real protective-order smoke test on the production account under the current account mode, order parameters and endpoint version (§3.1, §9.2). Test-order acceptance is never evidence of protection. Any change to these invalidates the sign-off until the smoke test is repeated. |

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

### 2.1 Capability boundary enforcement

- CI import contracts (e.g., `import-linter`) forbid `strategy`, `features`, `universe` and `research` from importing `execution`, `transfers`, exchange client libraries or credential loaders.
- Strategies are pure functions over immutable inputs that return signals; they never receive an adapter, client or session object.
- Credentials are read only in the runtime composition root while constructing adapters; offline/research entry points never load them.
- T-13 is therefore a static CI check plus a runtime test that a strategy cannot obtain an order-capable object.

## 3. Operating modes and authorization

| Mode | Data | Fills | Strategy authorization | Orders/transfers | Gate |
|---|---|---|---|---|---|
| `OFFLINE` | Historical immutable snapshots | Deterministic simulated | Versioned research candidate **permitted without promotion approval** | No real side effects | Default; M1/M2 |
| `PAPER` | Mainnet public feeds; optional read-only account data (§3.1) | Simulated, cost-aware; orders recorded, never sent | Explicit approval for **paper pilot** tied to strategy/config digest | No real side effects | M4 |
| `LIVE` | Exchange feeds/account | `LIVE_SHADOW`: test-order endpoint + simulated fills; `LIVE_ARMED`: exchange-confirmed (§3.1) | **Separate** explicit live approval and operator arming; paper approval is insufficient | Explicitly authorized only | M5 |

- All modes retain appropriate risk checks. `OFFLINE` experimentation may simulate rejected or extreme risk hypotheses under labeled test configurations; it does **not** grant live-risk authorization. Research candidates must have versioned configuration, inputs and results.
- Approval scope is tied to **mode + strategy/config digest**. Promotion between `OFFLINE`, `PAPER` and `LIVE` is never automatic and may be withdrawn. A candidate that fails research gates cannot gain paper/live approval merely by changing execution mode.
- A mode is fixed at process startup; switching requires shutdown and explicit reauthorization.
- Live requires separately provisioned credentials, approved live strategy/config digest, environment/account checks, and a manual arming procedure. **OPEN:** precise authorization mechanism, expiry, and dual confirmation.
- An operator may pause/stop entries at any time. Recovery from `HALTED` requires reconciliation, an audit event, and explicit operator approval; approval does not imply automatic phase reset.

### 3.1 Shadow execution and staged LIVE (no testnet)

No exchange testnet is used in any milestone. Exchange-facing behavior is exercised against **mainnet** in shadow form before any real order.

**`PAPER` = mainnet shadow mode (M4):**
- Market data, instrument filters, funding and order book come from Binance mainnet public endpoints.
- An optional **read-only** API key may read real account balances, margin mode, position mode, leverage and fee settings for account snapshots and §7.6 precondition checks. If a key is configured, PAPER verifies its permissions at startup via the exchange API-restriction query and refuses any key with trading, transfer or withdrawal permission (T-34). PAPER can start on public data alone.
- Every order is built exactly as `LIVE` would build it: quantized quantity and prices, flags, client order ID. It is validated locally against the live filters and recorded as a `SHADOW_ORDER` with its full payload. It is **never transmitted**. The paper adapter has no code path to any order endpoint, including the test-order endpoint.
- Fills are simulated from live mainnet prices/order book with the versioned cost model.

**`LIVE` stages (M5):** each stage requires live approval, operator arming and a restart with explicit reauthorization, per §3.
1. **`LIVE_SHADOW`:** live account and trading key. Every order is sent **only** to the exchange test-order endpoint (`POST /fapi/v1/order/test`), which validates parameters against the production account without reaching the matching engine. The exchange response is recorded with the shadow order, fills are simulated as in `PAPER`, and transfers are simulated only. Conditional/protective orders are assumed **not** covered by this endpoint (§9.2) and default to `NOT_VALIDATED`. Any order type the endpoint cannot validate is recorded as `NOT_VALIDATED` and must be covered by step 2. Runs for a predeclared observation window.
2. **Protective-order smoke test:** a small number of real orders at minimum compliant size, under explicit operator approval for that test only. It validates the §9.2 behaviors no test endpoint can show: stop acceptance and visibility, `reduceOnly`/`closePosition`, cancel/replace without a coverage gap, partial-fill handling and emergency flatten.
3. **`LIVE_ARMED`:** real orders under the approved live configuration.

A test-order acceptance confirms request validity only; it never establishes protective-order behavior or execution quality. The smoke test is a **hard gate** for `LIVE_ARMED` (INV-12).

## 4. Canonical time, data and provenance

### 4.1 Time semantics

- Store timestamps in UTC with exchange event time, observation/as-of time, ingestion time, and dataset version where applicable.
- Candle `[open_time, close_time)` is eligible only after close and after the configured availability delay. A decision at *t* cannot use incomplete candles or a later snapshot.
- Universe selection for day D uses information available before its decision boundary (candidate: through completed day D−1). Cadence is research-pending.
- Replays must run on an injected deterministic clock, not system wall time.

### 4.2 Data contracts

| Contract | Required fields (minimum) | Validation |
|---|---|---|
| `InstrumentSnapshot` | instrument ID (symbol + listing epoch/onboard date), symbol, contract type, contract multiplier where encoded in the symbol, listing/delisting validity, trading status, filters, effective/as-of times, source/provenance | Time-appropriate USDT perpetual eligibility; a delisted-then-relisted ticker is a distinct instrument; no series stitching across epochs |
| `Candle` | symbol, interval, open/close times, OHLCV, quote volume, source, availability time | Unique key, contiguous series, sane prices/volumes |
| `FundingObservation` | instrument ID, settlement time (`fundingTime`), funding rate, availability time, source, provenance | Settlement events come from exchange records; the simulator posts funding at recorded settlement times, never on a computed schedule; never apply future funding (§4.3.1) |
| `FundingScheduleObservation` | instrument ID, interval in effect, next funding time, cap/floor where published, effective/as-of time, derivation method, provenance | Point-in-time; interval never assumed to be 8h (§4.3.1) |
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

**Expected coverage and forward capture:** Binance public endpoints are expected to expose only current instrument filters (to be confirmed in M1), so most historical filters will be `RECONSTRUCTED`. From M0, a credential-free forward-capture job records daily `exchangeInfo` and `fundingInfo` snapshots, periodic `premiumIndex` snapshots (all symbols) and periodic order-book/spread samples for the candidate universe. These are `VERIFIED` from their capture time onward and are the preferred basis for promotion-grade feasibility and cost evidence on later periods.


### 4.3.1 Funding evidence

Binance `fundingInfo` lists only symbols whose funding cap, floor or interval has been adjusted, so its absence says nothing definitive about a symbol's interval, and a current snapshot says nothing about the past. Evidence sources and tags:

| Source | What it proves | Tag |
|---|---|---|
| Settlement history (`fundingRate` endpoint; public archive where coverage is verified) | When funding was actually settled and at what rate | `VERIFIED` events |
| Forward `premiumIndex` snapshots (all symbols: `nextFundingTime`, mark price) | Next scheduled settlement as observed at capture time | `VERIFIED` from capture time onward |
| Forward `fundingInfo` snapshots | Interval and cap/floor for listed (adjusted) symbols at capture time | `VERIFIED` for listed symbols |
| Interval inferred from spacing between settlements | Schedule in effect over a period | `RECONSTRUCTED` |
| Documented default applicable to the instrument and period | Explicit research scenario for a schedule with missing evidence | `RECONSTRUCTED` (source and applicability recorded in the versioned configuration) |

- **Accounting uses events, not schedules.** The simulator posts funding at the recorded settlement times and rates (§4.5). A derived interval is used only for decision-time estimates, such as the fee/funding reserve (`FEE_FUNDING_RESERVE`) and the expected next settlement.
- **Decision-time evidence.** For a decision at time *t*, estimates may use only observations and documentation available by *t*. An interval inferred from settlement spacing becomes available only after all supporting records are available. A later settlement can support a later estimate. It cannot confirm an interval for an earlier decision or change that decision's provenance.
- **Missing schedule evidence.** Absence from `fundingInfo` supplies no interval. Use a documented default only in an explicitly configured `RECONSTRUCTED` research scenario. Without supporting evidence or such a scenario, tag the interval `UNAVAILABLE`.
- **Inference rules** (frozen in M1 under SC-01): a declared timestamp-jitter tolerance; detection of interval changes inside a window; handling of listing start and of missing settlements.
- **Gaps:** a missing settlement record during a period in which a position may be open is `UNAVAILABLE`. That symbol-period is excluded or handled by a declared conservative rule (§4.4); values are never interpolated.
- **Cap/floor:** store published funding-rate caps/floors with effective times for cost stress tests.

### 4.4 Data failure behavior

Missing bars, stale feeds, uncertain exchange filters, invalid timestamps, or insufficient warm-up invalidate the affected signal. Critical uncertainty yields **no trade**. Backfills are versioned, never silently overwrite research evidence. A position with missing valuation data must not be silently marked at a stale price: fail the affected replay or use an explicitly specified and stress-tested conservative policy.

### 4.5 Simulated equity accounting contract (M1 exit requirement)

M1 must implement and test the **simulation accounting contract** before M1 is accepted; M2 uses this identical accounting for equity-trigger, halt, expectancy, and recovery-feasibility research. This is separate from **M3 exchange account and transfer reconciliation**.

- **Position and cash basis:** signed quantity `q` is positive for long and negative for short. Track executed fill prices, position cost basis, realized trading PnL, trade fees, funding cashflows and any other modeled cash adjustments as distinct immutable ledger events. Entry/exit commissions are charged on fills; a closed position realizes trading PnL exactly once. An open position's unrealized PnL is `Σ[q × (mark_price − cost_basis_price)]` under the declared linear USDT-perpetual contract convention.
- **Canonical simulated equity:** `sim_wallet = initial_wallet + cumulative_realized_trading_PnL + cumulative_funding_cashflow − cumulative_fees + other_explicit_cash_adjustments`; `sim_equity = sim_wallet + unrealized_PnL`. Posted funding and fees must not be charged again at trade exit. Margin reservation reduces *available collateral* but is **not** subtracted a second time from equity. Realized PnL and mark-to-market PnL must not overlap.
- **Mark price and source:** M1 first assesses coverage of Binance historical mark-price klines (`markPriceKlines` endpoint and the public data archive) and uses them where available and causally valid. Only for documented coverage gaps may M1 use an explicitly labeled deterministic `CANDLE_CLOSE_PROXY` for close-of-bar equity snapshots and a corresponding disclosed bar-open proxy for open-event valuation; proxies are **not** claimed to be Binance mark prices. Execution prices (including adverse slippage) remain distinct from valuation marks. The proxy limitation and margin/liquidation sensitivity must be disclosed in M2 evidence; a required mark missing at an open position must not silently reuse an old mark.
- **Stop trigger and liquidation price basis:** liquidation is evaluated on the mark-price path using point-in-time, provenance-tagged maintenance-margin tiers. Protective stops trigger on the price type declared by the versioned `stop_working_type` (`MARK_PRICE` or `CONTRACT_PRICE`), which must equal the planned live setting. The simulator evaluates each on its own price path and flags bars where liquidation could precede the stop.
- **Event ordering:** resolve an explicit, deterministic timestamp/priority ordering for exchange settlement, pending order fills, protective stops, funding postings and snapshots. Apply each event once to the position held at the actual modeled settlement time. When intrabar order/funding/stop chronology cannot be reconstructed from OHLC, enforce a documented conservative rule and flag the ambiguity; never imply that OHLC provides unseen tick ordering. The chosen rule and tied-timestamp behavior must be frozen and regression-tested before M1 exit; ownership is defined in §4.5.1.
- **Snapshot cadence and trigger semantics:** emit reconciled simulated snapshots after each modeled fill/funding/stop event and at each completed bar close, with timestamp, mark source, position state and wallet/equity breakdown; record which snapshots are eligible for M2's 100 USDT observation and 30 USDT halt checks, evaluated on `cycle_equity` (§7.3.1). Do **not** infer a trigger merely from a candle's favorable high/low when no eligible event-time equity is observable. If the model restricts trigger evaluation cadence, declare it before M2 research and measure sensitivity; do not retroactively change it after observing outcomes.
- **Accounting acceptance:** for each event/snapshot, cash ledger totals reconcile to `sim_wallet`, and `sim_equity − sim_wallet == unrealized_PnL` within a predeclared numerical tolerance. Verify flat-position equity equals wallet, realized/unrealized transition on partial and complete closes, funding charged to the correct holder, fee/slippage accounting, and deterministic replay with adverse gaps. Exact priorities, price bases, numeric precision and valuation timing are specified under SC-02 in §13.1.


### 4.5.1 M1/M2 boundary for simulation semantics

- **M1 owns how events are processed (SC-02):** fill model and fill prices, gap handling, intrabar stop-trigger evaluation and the conservative ambiguity rule, entry and stop in the same bar, funding posting, liquidation versus stop, tied-timestamp priority, and snapshot emission.
- **M2 owns what the strategy emits and when (SC-04):** decision timestamp, order type and submission bar (e.g., next-bar open), indicator indexing and warm-up, stop-level computation within the bar-close trailing rule (§6), universe-exit handling, and the trigger/halt evaluation cadence (§7.3.1).
- **Delayed trailing (M2 must account for it):** a trailing update takes effect at the first modeled open at or after its source close and intent are available (SC-02, §13.1). With any positive candle availability lag, that is one full bar after the source close, and the old stop governs the bar in between. Live replacement takes about a second. SC-04 must declare how the strategy's ATR exit relies on this rule, and M2 must report sensitivity, for example against a zero-lag manifest, before the holdout.
- M1 rules are frozen at M1 acceptance and are **not** changed in response to M2 research results. A necessary change is a new, versioned M1 release made before the M2 holdout; all affected results are rerun and the change is reported.

## 5. Point-in-time universe and execution feasibility

The universe pipeline at each decision time:

1. Discover historically tradable Binance USD-M **USDT perpetual** instruments, including later-delisted symbols when evidence supports them.
2. Apply status, history-quality, liquidity and exclusion rules using only prior completed observations.
3. Candidate liquidity ranking: trailing 30-day mean quote volume, top 30 (**research-only**).
4. Compute candidate 14-day **signed** return; positive momentum may support long eligibility, negative momentum short eligibility. Do not turn absolute negative returns into long signals.
5. Test symbol-specific filters, order quantity, stop distance, margin, fee/funding reserve, and liquidation buffer at **50 USDT**. A major such as BTC/ETH has no exemption.
6. Persist selected symbols, ranks, exclusion reasons, input provenance and config hash. No eligible symbol means no trade.

**Sizing feasibility:** Let `risk_budget` be approved maximum modeled loss, `stop_distance` be the absolute entry-to-stop price distance, and `cost_buffer(q)` conservative costs and stressed fill loss. Find an exchange-compliant quantity `q` such that `q × stop_distance + cost_buffer(q) <= risk_budget`, while satisfying margin, liquidation distance and symbol filters. Quantize down to allowed steps; if minimum order cannot satisfy all constraints, reject. Exact sizing rules are fixed in §5.2; maintenance-margin tiers remain **OPEN** pending exchange compatibility and research.

### 5.1 Micro-capital execution eligibility contract (M1 hard gate)

For every candidate entry, compute and persist a deterministic `EligibilityDecision` containing symbol, decision/as-of time, filter provenance, side, entry/stop assumptions, candidate risk budget, requested and quantized quantity, notional, initial/maintenance margin assumptions, leverage, estimated fee/funding reserve, stressed stop loss, liquidation buffer, result and reason codes. Use exchange-specific effective filters; do not hardcode a universal minimum notional. Quantity may be rounded **down** to a valid step; never round up to satisfy minimum notional if doing so violates risk. Reject any order that fails **any** constraint. Leverage can reduce initial margin but cannot relax the stop-loss risk budget.

**Deterministic rejection taxonomy:** `MISSING_PIT_FILTER`, `UNVERIFIED_CRITICAL_FILTER`, `STALE_METADATA`, `INSUFFICIENT_HISTORY`, `MIN_NOTIONAL`, `MIN_QTY`, `STEP_SIZE`, `PRICE_TICK`, `RISK_BUDGET`, `INSUFFICIENT_COLLATERAL`, `FEE_FUNDING_RESERVE`, `LIQUIDATION_BUFFER`, `HALT_HEADROOM`, `SPREAD_COST_UNCERTAIN`, `UNKNOWN_CONSTRAINT`. Multiple reasons may be reported; critical unknowns fail closed. Filter uncertainty and reconstruction-dependent eligibility must be explicitly separated from verified cases.

**M1 mandatory evidence:** Produce a versioned **50 USDT Execution Feasibility Report** covering the candidate point-in-time universe and research windows. Compute it for every Ignition **and Growth** risk candidate at 50 USDT (Growth also starts at 50 USDT) and at representative Growth equity levels (e.g., 100 and 250 USDT). Include: attempted candidates, executable count and rate, rejection counts by reason/symbol/regime, size/notional/margin distributions, cost/stop-distance sensitivities, and the binding constraint (`RISK`/`MARGIN`/`LIQUIDATION`/`LIMIT`, §5.2) and the `risk_utilization` distribution per symbol. A margin-bound order below 100% risk utilization counts as executable, not rejected. Distinguish *technical eligibility* from *strategy profitability*; report coverage and uncertainty rather than treating reconstructed filters as verified. M1 acceptance requires the report and correct rejection behavior, **not a preselected minimum pass rate**. A numeric promotion-grade feasibility threshold is preregistered before the M2 holdout (SC-03). If no feasible orders exist under defensible assumptions, record a no-trade/feasibility blocker rather than weakening risk controls.


### 5.2 Risk base, initial stop and rounding

- **Risk base:** `phase_risk_budget = risk_pct × cycle_equity` (§7.3.1) at decision time, in every phase, then capped by the halt-headroom rule (§8.1). A fixed-reference variant (`risk_pct × 50 USDT`) may be studied only as a preregistered research variant.
- **Initial stop distance:** `d = k × ATR(14)` from the completed signal bar (candidate `k = 2.5`, the same multiplier as the trailing exit).
- **Quantity (most restrictive cap, then quantize, then validate):**
  1. Compute every upper bound on quantity:
     - `q_risk = risk_budget / (d + c)`, where `c` is the conservative per-unit cost (entry and exit fees at taker rate plus stressed slippage on both sides);
     - `q_margin = (available_collateral × u × L_max) / entry_price`, where `available_collateral` excludes opening excess and the fee/funding reserve (§7.1.1) and `u` is the permitted margin utilization;
     - `q_liq`, the largest quantity whose liquidation price, under the declared point-in-time maintenance-margin tier and the allocated isolated margin, stays beyond the stop by the liquidation buffer;
     - `q_limit`, the exchange maximum quantity (`LOT_SIZE`/`MARKET_LOT_SIZE` max) and any configured exposure cap.
  2. `q_max = min(q_risk, q_margin, q_liq, q_limit)`. Record which bound binds (`RISK`, `MARGIN`, `LIQUIDATION`, `LIMIT`).
  3. `q = floor_to_step(q_max)`. Never round up.
  4. Validate `q` against **all** exchange filters (`minQty`, step size, `minNotional`, maximum quantity, price tick and any price-band filter) and re-check risk, margin and liquidation buffer at the rounded values. Reject only if a filter or check fails, with the matching reason code.
  - An order that uses **less than 100% of the risk budget** because margin, liquidation or a limit binds is **valid**, not rejected. Record `risk_utilization = modeled_risk(q) / risk_budget` with the decision, and report its distribution in SC-12.
- **Stop placement preserves distance:** the initial stop is placed at `actual_fill_price − d` for longs and `+ d` for shorts, not at a level fixed from the signal close, so a gap between signal and fill does not change per-unit risk.
- **Price rounding:** stop prices are rounded to the tick **toward** the entry (tighter, never wider), so the realized distance is `<= d`. Quantity is rounded down to the step. If either rounding makes the order non-compliant, reject.
- **Binding-constraint diagnostic:** ignoring costs, if `d / price < risk_budget / (E × L_max × u)` the margin/leverage cap binds and the order is sized below the risk budget; this is still valid. Only if the minimum compliant order `N_min = max(minNotional, minQty × price)` would exceed the risk budget (`d / price > risk_budget / N_min`) or the margin/liquidation caps does the entry become infeasible (`MIN_NOTIONAL`/`MIN_QTY` with `RISK_BUDGET`, `INSUFFICIENT_COLLATERAL` or `LIQUIDATION_BUFFER`). SC-12 reports the binding constraint and the infeasibility reasons separately.

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
- **OPEN before M2 (strategy semantics, SC-04):** exact candle-close decision time, next-bar execution, volume-window indexing, Donchian band exclusion of signal candle, ADX/ATR/EMA warm-up, stop-level computation, and handling of open positions when a symbol leaves the universe or its momentum sign flips. Intrabar stop ordering, funding timing and tie-breaking are simulator rules frozen in M1 (§4.5.1), not M2 decisions.
- **Trailing-stop implementation (decided):** the ATR trailing stop updates only at completed bar close and ratchets only in the favorable direction. Live, it is an exchange-hosted stop order replaced by placing the new order before cancelling the old (§9.2). Research models exactly this step-wise level, never continuous intrabar trailing. Exchange-native percentage-callback trailing orders are not used for the ATR exit.
- Evaluate baseline without pyramiding first. Additional families and adaptive selector are research-only until separately promoted.

### 6.1 Adaptive selector promotion contract (future)

Before selector holdout, freeze features, selection cadence, costs, multiple-testing method, and comparators: (a) best single strategy selected **only on development data**, frozen for holdout; (b) simple predeclared baselines including no-trade when relevant. Require incremental net-of-cost benefit with credible uncertainty. Ex-post best comparator cannot be used to claim promotion.

## 7. Capital and operational state machines

### 7.1 Capital phases

| Phase | Entry | Allowed behavior | Transition |
|---|---|---|---|
| `IGNITION` | Explicitly authorized new cycle, reference 50 USDT | Risk-authorized candidate trades, no pyramiding | Eligible observed equity (`cycle_equity`, §7.3.1) >=100 -> `RECOVERY_PENDING` |
| `RECOVERY_PENDING` | Recovery intent durably recorded | **No new entries**; settle, reconcile, possibly transfer | Confirmed transfer + post-transfer reserve -> `GROWTH`; after a shortfall, only the operator resolutions of §7.4.1 |
| `GROWTH` | 50 USDT transfer confirmed; active Futures equity >=50 plus reserve | Lower-risk approved strategy | Active equity >=500 -> `VAULT` |
| `VAULT` | Verified active Futures equity >=500 | Approved preservation policy; optional validated profit locking | No automatic phase regression |

**Ignition fixed-loss policy:** Reference is fixed at 50 USDT throughout the authorized cycle. `cycle_equity <= 30 USDT` (§7.3.1) triggers an operational halt and protective/flattening procedure. This is **40% loss from initial capital**, **not** 40% drawdown from peak equity. A path 50→80→45 does not trip this fixed threshold; additional peak-drawdown controls are **OPEN**. The 30 USDT threshold is not a guaranteed loss floor.

**Growth/Vault candidate preventive halts:** 12% / 8% from phase high-water mark, respectively; **research-only**, not approved live values. Exact phase-high reset and treatment of ACGS-initiated transfers are **OPEN**; the equity basis is `cycle_equity` (§7.3.1) and external inflows are excluded per §7.6.

### 7.1.1 Capital-cycle records and lifetime limits (PRD §5.2.1)

- `capital_cycles` stores cycle ID, start time, committed capital (initial or top-up), status `OPEN`/`CLOSED`, closure reason (`FIXED_LOSS_HALT` or `OPERATOR_CLOSE`), residual, and post-mortem reference/digest. Lifetime counters (`cycles_started`, `committed_capital_total`) are derived from the ledger, never stored as editable configuration.
- `FIXED_LOSS_HALT` closes the cycle only after exposure is flat and reconciled. The operational `HALTED` latch remains until operator acknowledgment. Other halts do not close the cycle.
- Authorization of **every** cycle, including the first, is rejected unless all hold:
  - live approval is not suspended;
  - there is no open exposure or order, and the USD-M wallet holds between 50.00 and 50.50 USDT;
  - `cycles_started < 2` and `committed_capital_total + top_up <= 100`.

  The **first** cycle needs no previous-cycle closure or post-mortem; its committed capital is the initial 50 USDT. The **second** cycle additionally requires the first cycle to be `CLOSED` with its post-mortem recorded. The two-cycle and 100 USDT lifetime limits are unchanged.

  The reference remains exactly 50 USDT. The tolerance exists only for amounts that are impractical to remove. `opening_excess = wallet_at_cycle_start − 50` is recorded at cycle start and subtracted from `cycle_equity` (§7.3.1), so cycle equity starts at exactly 50. The excess is not trading capital: it never increases the risk budget, never counts toward the 100 USDT trigger, never widens halt headroom, and is excluded from the available collateral used in margin, sizing and feasibility checks (§5.2, §8). Implementation must test this against the exchange's available-margin figure, which will include the excess (SC-06).
- Residual funds of a closed cycle are never traded. ACGS never deposits or tops up by itself. A top-up is an operator action, classified through §7.6 and recorded as committed capital.
- Lifetime limits are code constants checked against the ledger. Configuration cannot raise them; a PRD revision is required.

### 7.2 Orthogonal operational states

- `RUNNING`: entry may be considered only if all other gates pass.
- `PAUSED`: no new entries; continue exposure protection/reconciliation.
- `RECOVERING`: controlled restoration of exchange/account truth; no new entries.
- `HALTED`: latched safety stop; no new entries; emergency management only, manual authorization required to resume.

Safety faults (stale data, unknown orders, unprotected position, reconciliation mismatch, risk breach) latch pause/halt as specified in a versioned severity matrix (**OPEN before M3/M4**). Restart does not clear a halt.

### 7.3 Recovery transaction protocol

**Agreed baselines:** observation threshold 100 USDT, transfer amount 50 USDT, minimum post-transfer active equity 50 USDT, additional operational reserve >=0 (to be specified). These are distinct from transfer execution eligibility.

1. Persist unique `recovery_cycle_id` and `RECOVERY_PENDING` intent **before** any side effect; atomically block entries.
2. Cancel/resolve open orders, including conditional/algo orders (§9.2), and flatten/settle exposure; reconcile no residual positions or contingent re-entry orders.
3. Obtain fresh exchange evidence of realized wallet, available/transferable funds, fees, funding, pending costs and transfers.
4. Require `transferable_funds >= 50` and **post-transfer active equity >= 50 + additional_operational_reserve**, net of remaining costs. The simple settled-equity threshold is `100 + additional_operational_reserve` only if no additional costs remain.
5. If checks fail, stay `RECOVERY_PENDING`, block entries, alert operator; no automatic return to Ignition or trading to chase the shortfall.
6. In M3 simulate a unique transfer intent and acknowledgment. In M5 submit only after explicit permission; on ambiguous acknowledgment, query exchange transfer history **before any retry**.
7. Mark success only when transfer ID/history, both wallet balances, reserve, and durable ledger reconcile. Then enter `GROWTH` once.

**Exactly-once outcome** is a required logical property, not an assumption about exchange API idempotency. Maintain a durable intent/attempt ledger and refuse blind retries. **OPEN before M3:** exchange-specific equity/realized/unrealized account fields and mark-price reconciliation, exchange snapshot freshness/cadence, transferable-funds and reserve formula, transfer timeout/retry policy and crash consistency. **M1 simulated equity accounting and event timing are resolved under SC-02 before M1 acceptance, not deferred to M3.** **OPEN before M5:** supported internal-transfer endpoint/permissions and account/regional eligibility.

Protected Spot funds are excluded from futures trading capital but remain subject to exchange/custody risk; no automatic redeposit.

### 7.3.1 Recovery trigger and halt measure (resolves the equity-definition part of OD-02)

The agreed 100 USDT observation trigger and 30 USDT halt threshold are **unchanged**. This section defines the measure they are evaluated against.

- **Cycle equity:** `cycle_equity = conservative_NLV − unapproved_external_inflows − opening_excess` (opening excess per §7.1.1), where `conservative_NLV = equity_mark − Σ est_close_fee(p) − Σ stressed_close_slippage(p) − known_accrued_costs` over open positions `p`. `est_close_fee` uses the taker rate for the full position; `stressed_close_slippage` uses the same versioned stress model as the simulator cost model (SC-02); `known_accrued_costs` covers costs already determined but not yet posted. When flat, `conservative_NLV = wallet`; costs already posted are never deducted again. External inflows are defined in §7.6.
- **Rationale:** closing costs are strictly positive and the minimum settled execution threshold is `100 + additional_operational_reserve`. A trigger on gross mark equity would therefore settle below the threshold in most cases, increasingly so at high sampling frequency, while bar-close research would hide this through bar overshoot. Deducting estimated closing costs makes the observation an estimate of what flattening would realize. It reduces but does not eliminate shortfall; §7.4.1 handles the remainder.
- **Non-double-counting:** because `conservative_NLV` already deducts stressed closing costs, those costs must not be counted again in `additional_operational_reserve` (PRD §5.3). Any positive reserve creates a structural gap between the 100 USDT trigger and the execution threshold; it must be justified by a cost not already captured, and the M2 shortfall metric must be reported for the chosen reserve value.
- **Same measure for halt:** the 30 USDT Ignition halt is evaluated on `cycle_equity`. Because it is never above gross mark equity, this is a permitted more-conservative rule under PRD §5.2.
- **Evaluation cadence parity:** the cadence at which `cycle_equity` is evaluated for trigger/halt is a versioned configuration value. It must be identical in `OFFLINE`, `PAPER` and `LIVE`, or research must evaluate at a granularity at least as fine as live (e.g., 1m mark-price bars if live evaluates every minute). Declare it before the M2 holdout and report sensitivity to cadence.
- **Required M2 metric:** recovery shortfall rate `P(settled_equity < 100 + additional_operational_reserve | trigger)` under the declared cost stress grid, reported with uncertainty alongside the target-before-halt probability, and the **dead-zone rate** `P(100 <= settled_equity < 100 + additional_operational_reserve | trigger)` (§7.4.1).
- **Reserve scenarios (preregistered before the M2 holdout, SC-03):**
  - (a) `additional_operational_reserve = 0` as the research baseline;
  - (b) at least one positive scenario justified by **cost-model error**, i.e., the distribution of realized minus modeled closing cost on development data (e.g., an upper percentile). It is never justified by costs already deducted in `conservative_NLV`;
  - (c) every **exact** reserve value intended for operation, registered as a promotion candidate under the declared multiple-comparison rules, so M3 can use it without a fresh holdout.

  Designate one **primary** scenario before the holdout for the M2 pass/fail decision. Report shortfall and dead-zone rates for each scenario. In M3 (SC-06), the exact operational reserve must meet the preregistered M2 criteria. If the value differs from the primary scenario, evidence from the original M2 holdout qualifies only if the exact value was preregistered for promotion evaluation under the declared multiple-comparison rules and passed those criteria. Otherwise, preregister a separate evaluation on a fresh holdout using the same M2 criteria. An evaluated range or a sensitivity result alone is insufficient.
- Reserve value, exchange field mapping and snapshot freshness remain under OD-02/OD-03 and SC-06.

### 7.4 Recovery substate and crash-consistency contract (M3 hard gate)

Within `RECOVERY_PENDING`, persist one of `FREEZE_ENTRIES`, `FLATTENING`, `RECONCILING`, `TRANSFER_PENDING`, `VERIFYING`, `SHORTFALL_HOLD`, `COMPLETED`, `CLOSED_UNRECOVERED`, or `MANUAL_INTERVENTION`. These are **workflow substates**, not new capital phases. Every transition records `recovery_cycle_id`, source/target substate, timestamp, evidence digest, operator/automation actor and a monotonic version in the durable store. The entry freeze and recovery intent must commit atomically before flattening or transfer side effects. `COMPLETED` is reachable only after independent transfer-history, wallet and reserve reconciliation.

**Crash/timeout rules:** After restart, reconstruct the last durable intent and query authoritative order, position, balance and transfer evidence before taking another side effect. Unknown close-order or transfer acknowledgment must not cause blind resubmission. If transfer history cannot uniquely establish success/failure, remain frozen in `MANUAL_INTERVENTION` (or `VERIFYING` with a bounded reconciliation policy); never infer success from a local timeout or a single wallet snapshot. Reconcile contingent orders and residual exposure before transfer. Manual resolution must be audited and cannot override minimum reserve or bypass evidence requirements.

**M3 mandatory fault suite:** Inject process termination before/after each persisted transition, before/after sending close/transfer requests, lost/duplicated/out-of-order acknowledgments, partial fills, stale wallet snapshots, insufficient post-settlement reserve, ambiguous transfer history and temporary API outage. Assert no duplicate logical recovery, no new entry while pending, no transfer when eligibility is false, and no `GROWTH` without verified 50 USDT recovery plus required active reserve. The simulated transfer adapter must not contact a real exchange.

### 7.4.1 Shortfall hold and operator resolutions

`SHORTFALL_HOLD` is entered from `RECONCILING` when exposure is flat and reconciled, no transfer attempt is outstanding, and the §7.3 step 4 eligibility check fails (e.g., settled 98). It is distinct from `MANUAL_INTERVENTION`, which covers ambiguous evidence. While in `SHORTFALL_HOLD`: no new entries, no transfer, operator alerted, and the shortfall amount recorded with its decomposition (price move after trigger, fees, slippage versus model, funding, reserve).

Only these explicit, audited operator resolutions are permitted, each preceded by a fresh reconciliation:

| Resolution | Effect | Constraints |
|---|---|---|
| `RECHECK` | Re-run reconciliation and the eligibility check without trading (e.g., after a pending posting settles) | Remains `RECOVERY_PENDING`; proceeds to `TRANSFER_PENDING` only if eligibility now passes |
| `RESUME_IGNITION` | Close the attempt as `CLOSED_UNRECOVERED`; capital phase returns to `IGNITION` in the **same** capital cycle | Fixed 50 USDT reference and 30 USDT halt unchanged; no counter or reference reset; automated preconditions below must pass; recovery trigger re-arms under §7.3.1 |
| `CLOSE_CYCLE` | Close the attempt and the capital cycle (`OPERATOR_CLOSE`, §7.1.1) | A new cycle only under PRD §5.2.1 lifetime limits |

Never permitted: a transfer below 50 USDT counted as recovery; a transfer leaving less than `50 + additional_operational_reserve` active; entry into `GROWTH`; lowering the reserve to pass the check without a versioned approval made before the attempt; counting an external top-up toward eligibility (top-ups are permitted only between cycles, PRD §5.2.1).

**`RESUME_IGNITION` preconditions** (automated; the operator cannot waive them). The risk engine refuses the resolution unless all hold:
- **Cost model not breached:** the recorded shortfall decomposition shows realized closing fees and slippage within the stressed costs assumed in `conservative_NLV`. If realized costs exceeded them, the cost model is breached on the first occurrence. Resume stays blocked until the model is re-versioned with stress assumptions raised, never lowered, and the re-trigger then uses the new model.
- **No catch-up sizing:** the risk configuration digest equals the one in force at the trigger. `risk_pct`, the leverage ceiling and concurrency cannot be raised, and `halt_buffer` cannot be lowered, at resume.
- **Headroom remains:** `cycle_equity − 30 − halt_buffer` still admits at least one exchange-compliant minimum order under §5.2/§8.1 at current filters. Otherwise resume is refused and `CLOSE_CYCLE` is the expected resolution.
- **Below trigger:** the latest fresh, reconciled snapshot shows `cycle_equity < 100`. If settled `cycle_equity >= 100` but the reserve check failed (the dead zone `100 <= cycle_equity < 100 + additional_operational_reserve`), resume is refused and the system stays in `SHORTFALL_HOLD` with only `RECHECK` or `CLOSE_CYCLE`. Resuming would re-trigger recovery immediately, and trading to build the reserve is prohibited (PRD §5.3). Comparisons use decimal arithmetic with the tolerance fixed under SC-02; exactly 100 counts as reaching the trigger (T-43).
- Fresh reconciliation passes, account preconditions (§7.6) hold, and no external inflow is awaiting classification.

**Repeated shortfall:** after **two** `CLOSED_UNRECOVERED` attempts in one capital cycle, `RESUME_IGNITION` is no longer permitted in that cycle; only `RECHECK` or `CLOSE_CYCLE` remain. This prevents a cycle from repeatedly chasing the 100 USDT trigger through settlement losses.

### 7.5 Vault profit locking (optional)

Candidate: lock 40% of eligible realized profit on a defined doubling of post-vault reference equity. **Not an approved rule.** Reference basis, eligible profit, fees, reserve, cadence, minimum transfer, and repeat-trigger behavior are **OPEN**. Never transfer against unrealized PnL alone.

### 7.6 Account isolation and external cash flows

**Account isolation (M3 contract; M5 verification):** ACGS operates on a dedicated Binance account or sub-account whose USD-M Futures wallet is used exclusively by ACGS: no manual trading, other bots or unrelated positions. Availability is verified together with account/regional eligibility.

**Account preconditions:** checked at startup, after reconnect and before every recovery or Vault transfer. Any mismatch is a reconciliation discrepancy (INV-03) that blocks new entries.
- Single-asset margin mode (Multi-Assets mode off); only the USDT balance counts toward equity, other assets count as zero.
- BNB fee deduction disabled, so fees post in USDT; enabling it requires a versioned accounting change first.
- Isolated margin and the declared position mode per §8 and §9.2.

In `OFFLINE` these are declared configuration assumptions exercised by tests. In `PAPER` with a read-only key they are verified against the real account; without one they remain declared assumptions until M5.

**External cash flows:** any USD-M wallet balance change not explained by ledger events (realized PnL, fees, funding, ACGS-initiated transfers) is an `EXTERNAL_CASH_FLOW`, identified from exchange income/transfer history (exact fields under SC-06).
- On detection: latch `PAUSED`, block entries, alert, and record the flow in the ledger as an explicit external adjustment.
- External **inflows** are subtracted from `cycle_equity` (§7.3.1) and never count toward the 100 USDT trigger, the 500 USDT Vault threshold or a new high-water mark. External **outflows** are not added back, so they lower cycle equity and bring the halt closer. Both choices are conservative.
- Resume requires operator classification of the flow. An inflow may be approved as committed capital only as a between-cycles top-up under PRD §5.2.1 and §7.1.1; any inflow during an open cycle remains unapproved and excluded.
- In `OFFLINE`/`PAPER`, external flows occur only as explicit labeled test adjustments (`other_explicit_cash_adjustments`, §4.5) and follow the same rules.

## 8. Risk authorization

Risk is modeled adverse loss to a protective stop **plus conservative fees, slippage and stress fills**, not margin used or leverage. Exchange stops cannot guarantee the modeled loss.

| Phase | Candidate risk/trade | Candidate leverage ceiling | Authorization |
|---|---:|---:|---|
| Ignition | 8–12% | 10× | **Research-only; highly aggressive** |
| Growth | 1–2% | 5× | Research-only |
| Vault | 0.5–1% | 3× | Research-only |

**No live risk parameters are authorized.** `OFFLINE` research uses versioned candidate settings and simulated risk checks without strategy-promotion approval; `PAPER` and `LIVE` use separate mode-specific authorizations (Section 3). Live approval requires M2 evidence, 50 USDT sizing feasibility, stress tests, paper evidence and manual sign-off. Risk authorization evaluates phase, operational state, applicable mode/config, freshness, stop distance, exchange filters, margin/liquidation buffer, combined stop exposure, correlated positions, concurrency, funding/fee reserve and halt limits. Reject with auditable reason codes when any gate fails.

**M2 minimal simulated Ignition policy:** fixed 50 USDT initial reference, 100 USDT observation target, 30 USDT fixed-loss halt, candidate sizing/exposure caps and realistic costs, evaluated on `cycle_equity` (§7.3.1) with the halt-headroom cap (§8.1). Estimate target-before-halt and conditional recovery feasibility; **do not** model M3 as implemented or claim a real transfer occurred.

**OPEN:** exact max exposure, correlated risk, maximum positions, preemptive peak drawdown, daily loss, stop-to-liquidation buffer, candidate parameter grid, and portfolio loss budget. Do not infer these values from the 40% account halt.

### 8.1 Halt-headroom cap

A new entry's risk budget is limited by the distance to the active halt level, not only by the phase risk percentage. This applies to the M2 minimal simulated Ignition policy and every later mode.

```text
halt_headroom   = cycle_equity − halt_level − halt_buffer
open_risk       = Σ stressed loss from current mark to stop (open positions + resting entry orders)
risk_budget_eff = min(phase_risk_budget, halt_headroom − open_risk)
```

- `phase_risk_budget = risk_pct × cycle_equity` (§5.2).
- `halt_level` is 30 USDT in Ignition. In Growth/Vault it is the approved HWM-based halt level once OD-04 is resolved; until then, labeled research configs use the candidate values.
- Closing costs already deducted in `cycle_equity` are not counted again in `open_risk`.
- If `risk_budget_eff <= 0` or no exchange-compliant quantity fits it, reject with `HALT_HEADROOM`.
- `halt_buffer >= 0` is a versioned research parameter for gap/stop slippage beyond the stress model, fixed before the M2 holdout.
- Invariant: after any authorized entry, aggregate `open_risk <= halt_headroom`.
- Example: cycle equity 34, halt 30, buffer 0.5, phase budget 5, no open positions → effective budget 3.5 USDT.
- Concurrency: the default M2 Ignition baseline allows **at most one open position**; higher concurrency is a preregistered variant reported separately.
- The cap reduces, but cannot guarantee against, overshooting the halt (gaps, liquidation, API faults; PRD §5.2).

## 9. Order execution and reconciliation

### 9.1 Order lifecycle

`CREATED -> AUTHORIZED -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED/FILLED` with alternative `REJECTED`, `CANCELED`, `EXPIRED`, and `UNKNOWN` branches. Actual fills and position changes come from exchange evidence; acknowledgments alone do not establish execution.

- Durable order intent, deterministic client ID where supported, side, symbol, quantity, strategy/risk authorization IDs and expected protective exit.
- Handle partial fills, duplicate messages, out-of-order events, lost acknowledgments, cancellation races, and restart.
- Unknown status => no blind resubmission; query open orders, trades/fills and positions first.
- After each fill, reconcile exchange-hosted protective exit quantity/side and enforce a predefined emergency flattening path if protection cannot be confirmed.
- Confirm actual isolated-margin setting, position mode, leverage, filters and current conditional-order API behavior. Do not assume `STOP_MARKET`/`reduceOnly` combinations are universally supported.

### 9.2 Protective-order compatibility and emergency contract (M3 mock; M5 exchange gate)

Maintain a versioned compatibility matrix for the **actual** Binance account and API configuration: one-way versus hedge position mode, supported conditional-order endpoints and trigger types, `reduceOnly`/`closePosition` applicability, quantity rules, margin/position-mode requirements, cancel/replace semantics (including place-new-then-cancel-old trailing updates without a coverage gap), supported stop `workingType` values and protective-order visibility after fills. Do not assume a specific `STOP_MARKET` parameter combination works universally. Exchange-native protective coverage must be independently confirmed from exchange state, not merely from an order-submission acknowledgment.

**Conditional-order endpoint family:** public reports indicate that Binance moved USD-M conditional orders (e.g., `STOP_MARKET`) to a separate Algo Service in December 2025, placed via `POST /fapi/v1/algoOrder` and queried via separate algo-order endpoints and update events; the regular order endpoint may reject them. Until verified against the official change log at implementation:
- Treat protective orders as a separate endpoint family in the compatibility matrix.
- Reconciliation (§9.3), recovery flattening (§7.3) and protection confirmation must query and consume **both** regular and conditional/algo order state and update events. A stop's presence or absence in one family must never be inferred from the other (T-40).
- Assume the regular test-order endpoint does not validate protective orders. They default to `NOT_VALIDATED` in `LIVE_SHADOW` (§3.1).

On a fill with missing, rejected, expired, insufficient or uncertain stop coverage: immediately block new entries, latch the configured safety state, attempt the preapproved bounded emergency flattening procedure where safe, and reconcile residual positions and contingent orders. Repeated blind submissions are forbidden; unresolved exposure requires alerting and operator intervention. Partial fills, stop-size adjustments, rejected conditional orders, reconnects and crash-between-fill-and-stop are mandatory mock/fault scenarios by M3/M4. In M3/M4 the matrix is exercised only against mocks and shadow orders; exchange behavior is validated in M5 through `LIVE_SHADOW` and the protective-order smoke test (§3.1).

**M5 hard gate:** Validate the compatibility matrix and emergency behavior on the production account through `LIVE_SHADOW` and the protective-order smoke test (§3.1), then explicitly sign off the account-specific live configuration before `LIVE_ARMED`. Test-order endpoint acceptance alone does not establish protective-order behavior; unresolved restrictions or unsupported protection => **NO-GO**. No real orders are authorized by this SPEC.

### 9.3 Reconciliation

Before entries at startup/reconnect and after uncertainty, compare local intents, exchange orders/fills, positions, balances, protective exits, and transfer history. Classify mismatches as `RESOLVED`, `RETRYABLE`, or `CRITICAL`; critical mismatch latches halt and alerts. **OPEN:** precise cadence, staleness SLAs and emergency action matrix.

### 9.4 Pyramiding (P1)

Disabled in Ignition. Growth/Vault candidate: add after favorable 1.5 ATR movement, up to three layers, only if validated versus no-pyramiding baseline. Do **not** size adds as a direct fraction of unrealized profit. Each add must satisfy total-position and portfolio risk limits with blended fees, funding, stop, margin and liquidation risk.

## 10. Persistence, audit and security

- SQLite single-writer MVP; transactional updates for capital phase, operational latch, order intents, transfer intents, ledger and audit events. Research datasets/evidence are immutable and content-addressed/versioned.
- Minimal logical records: `config_versions`, `dataset_versions`, `universe_snapshots`, `research_evidence`, `capital_cycles`, `state_events`, `order_intents`, `fills`, `position_snapshots`, `risk_decisions`, `transfer_intents`, `transfer_attempts`, `capital_ledger`, `reconciliation_events`.
- Every risk decision and live order links to strategy version, data/config digest, phase, account snapshot and reason codes.
- Secrets injected at runtime; never in repo, logs, datasets or test fixtures. Restrict API permissions; no withdrawal permission; separate paper/live identity and credentials.
- **Key tiers:** `PAPER` holds at most a read-only key. `LIVE_SHADOW` and `LIVE_ARMED` use the trading key. Only the transfer adapter in `LIVE_ARMED` uses the transfer key; in `LIVE_SHADOW` transfers are simulated.
- **Transfer capability:** in `LIVE`, trading and transfer use separate API keys where the exchange supports separate permissions. The trading key has no transfer or withdrawal permission; the transfer key is used only by the transfer adapter; both are IP-restricted. The transfer adapter accepts only USD-M Futures → Spot transfers with amounts produced by the recovery/Vault workflows; any other direction or type is rejected in code and tested (T-32). ACGS never initiates Spot → Futures. After a confirmed recovery, the operator moves protected funds out of the bot account's reach per runbook. If separate permissions are unsupported, SC-10 must document the residual risk before arming.
- Backup and restore tests must preserve safety latches and transfer-intent history. **OPEN:** retention, backup cadence, recovery point and alert delivery SLAs.

## 11. Research protocol and promotion gates

Research must use chronological development/validation windows, walk-forward tests, and an untouched holdout. Costs include fees, funding, slippage/spread uncertainty, margin/filter feasibility and realistic fills. Compare no-trade and simple baselines, parameter sensitivity, regime breakdowns, and incremental value of universe ranking, filters and pyramiding.

**Required baselines and resampling:** (a) no-trade; (b) seeded random entry with identical universe, sizing, exits and costs, replicated enough to estimate its distribution; (c) the breakout with each filter removed (ablation). Bootstrap/Monte Carlo resamples **calendar-time blocks across all symbols** to preserve cross-sectional correlation and regime clustering. Report an effective sample size that accounts for concurrently open, correlated trades.

**Before opening a holdout**, pre-register numeric thresholds in a versioned protocol referenced by this SPEC:

1. Effective trade sample and minimum observation duration.
2. Net expectancy criterion and uncertainty method/bound.
3. Maximum acceptable drawdown and estimated ruin probability (with uncertainty).
4. Cost/slippage stress grid and survival criterion.
5. 50 USDT executable-order feasibility/pass rate.
6. Rules for multiple comparisons, reconstruction coverage and `PASS`/`FAIL`/`INCONCLUSIVE`.
7. For M2, simulated probability of 100 USDT observation **before** 30 USDT halt, and conditional feasibility of a settled 50 USDT recovery with 50 USDT retained plus reserve, including the recovery shortfall and dead-zone rates per preregistered reserve scenario (§7.3.1) at the declared trigger cadence, and the probability of recovery success within the PRD §5.2.1 lifetime limits (at most two cycles).
8. Execution-model tolerance used in live-cycle post-mortems (PRD §5.2.1): maximum acceptable deviation of realized fees, slippage and stop behavior from the model.

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
| T-27 | Gross mark equity >=100 but `cycle_equity` <100 after estimated close costs; later `cycle_equity` >=100 | No trigger, then trigger; the 30 USDT halt uses the same measure | M2/M3 |
| T-28 | Settlement shortfall (e.g., settled 98) | `SHORTFALL_HOLD`; only `RECHECK`/`RESUME_IGNITION`/`CLOSE_CYCLE`; resume keeps 50 reference and 30 halt and is refused on cost-model breach, changed risk digest or insufficient headroom; no transfer or `GROWTH`; after two shortfalls in a cycle only `RECHECK`/`CLOSE_CYCLE` | M3 |
| T-29 | External deposit lifts mark equity >=100 in Ignition; Multi-Assets mode or BNB fee deduction found enabled | Inflow excluded, no trigger, `PAUSED` pending classification; precondition mismatch blocks entries | M3 (sim); M5 (account) |
| T-30 | Cycle equity 34, halt 30, buffer 0.5, budget 5; second entry while a position is open | Effective budget 3.5 minus open risk; reject `HALT_HEADROOM` when no compliant quantity fits | M2/M3 |
| T-31 | Fixed-loss halt; new cycle with residual 28 + top-up 22; then a third cycle; top-up during an open cycle | Cycle `CLOSED` only after flat; every cycle only with wallet 50.00–50.50 and the excess excluded from `cycle_equity`; second cycle only with post-mortem; third cycle or committed capital >100 rejected; mid-cycle inflow unapproved and excluded | M3 |
| T-32 | Transfer adapter asked for Spot→Futures, another transfer type or an arbitrary amount; trading key used for a transfer | Rejected in code; no request sent | M3 (sim); M5 |
| T-33 | Signal close 100, fill gaps to 101; stop price falls between ticks | Stop placed at fill − d, rounded toward entry; realized risk <= budget | M1 |
| T-34 | PAPER started without a key or with a key that has trading/transfer/withdrawal permission; any PAPER code path reaching an order endpoint; `LIVE_SHADOW` order routed anywhere but the test-order endpoint | PAPER can start on public data alone and refuses keys with any prohibited permission; PAPER sends no order request of any kind; `LIVE_SHADOW` sends only to the test-order endpoint and performs no transfer | M4/M5 |
| T-35 | Ticker delisted and later relisted; multiplier contract symbol | Distinct instrument IDs; no stitched history or indicators across epochs | M1 |
| T-36 | Funding interval changes from 8h to 4h inside a research window | Funding posted at recorded settlement times; the derived interval change is detected and tagged `RECONSTRUCTED`; no 8h assumption | M1 |
| T-37 | ATR trailing update | Updates only at bar close and only in the favorable direction; live replacement places the new stop before cancelling the old | M1 (sim); M3 (mock); M5 (smoke test) |
| T-38 | Mark-price path reaches liquidation before the stop triggers on its declared price type | Liquidation modeled and flagged; no optimistic stop fill | M1 |
| T-39 | M5 protective-order smoke test at minimum size; order types marked `NOT_VALIDATED` in `LIVE_SHADOW` | Stop acceptance/visibility, `reduceOnly`/`closePosition`, gap-free replacement and emergency flatten confirmed and signed off, otherwise NO-GO for `LIVE_ARMED`; sign-off invalidated and repeated after any account-mode, order-parameter, endpoint or API change (INV-12) | M5 |
| T-40 | Protective stop lives in the conditional/algo order family; reconciler or recovery flattening queries only regular orders | Both families and both update streams queried; stop unconfirmed if not found in its own family; contingent algo orders cancelled before transfer | M3 (mock); M5 |
| T-41 | Tight stop where the margin/leverage cap binds before the risk budget; quantized quantity passes all filters | Order accepted below 100% risk utilization with binding constraint `MARGIN` recorded; no false rejection. A case where only rounding *up* would meet `minNotional` is still rejected (T-22) | M1 |
| T-42 | Symbol absent from `fundingInfo`; documented default scenario; interval inferred from settlement spacing with a record arriving after a decision; settlement record missing while a position may be open | Absent interval is `UNAVAILABLE` unless supported by evidence or an explicit default scenario. Default and inferred intervals are `RECONSTRUCTED`. Later records cannot affect the earlier decision. A settlement gap is `UNAVAILABLE` and the symbol-period is excluded or handled conservatively without interpolation | M1 |
| T-43 | Flat, reconciled settlement at `cycle_equity` 99.99; 100.00 with reserve 0; 100.00 with reserve 0.50; 100.20 with reserve 0.50; 100.50 with reserve 0.50 | Resume allowed if other preconditions pass; transfer eligible; `SHORTFALL_HOLD` with resume refused; `SHORTFALL_HOLD` with resume refused; transfer eligible. Decimal comparison; no immediate re-trigger loop | M3 |

## 13. Open contract register

| ID | Must be resolved by | Decision |
|---|---|---|
| SC-01 | M1 | Resolved for OFFLINE in §13.1. Historical coverage is recorded per dataset. |
| SC-02 | **M1, before M1 acceptance** | Resolved for OFFLINE in §13.1. Event and accounting semantics apply to M2 data and replay. |
| SC-03 | Before M2 holdout | Pre-registered numeric gates, sample size, confidence and multiplicity; recovery reserve scenarios, the primary scenario and exact operational reserve candidates (§7.3.1) |
| SC-04 | **M2, frozen before holdout** (with SC-03) | Strategy signal semantics, indexing, warm-up, stop-level computation, universe ranking cadence and trigger/halt evaluation cadence; **not** simulator event processing (§4.5.1) |
| SC-05 | **M2, frozen before holdout** (with SC-03); M3 production engine | M2 minimal Ignition sizing and target/halt simulation; `cycle_equity` measure and cadence (§7.3.1); halt-headroom cap (§8.1) |
| SC-06 | M3 | Exchange-specific account-equity/mark-price field mapping, snapshot freshness, realized/transferable balance reconciliation, costs and post-transfer reserve (the exact operational value must pass the M2 criteria, §7.3.1), external cash-flow detection fields (§7.6); **not** simulator PnL/event accounting |
| SC-07 | M3 | State transitions, peak guards, risk/exposure limits and halt recovery |
| SC-08 | M3 | Durable transfer-intent/reconciliation algorithm and ledger invariants |
| SC-09 | M4 | Paper pilot duration, alerts, fault-response and operational acceptance |
| SC-10 | M5 | Binance endpoint/permission/position-mode compatibility, account isolation and preconditions (§7.6), and live arming |
| SC-11 | Optional P1/P2 | Vault locking, pyramiding and adaptive selector promotion |
| SC-12 | M1 | Resolved for OFFLINE in §13.1 with the versioned feasibility report. |
| SC-13 | M3 mock; M4 shadow; M5 `LIVE_SHADOW` + smoke test | Protective-order compatibility matrix, emergency flatten safety contract, account-mode validation, trailing replacement without coverage gap, shadow-mode key and endpoint restrictions, conditional/algo endpoint family and smoke-test validity (INV-12) (T-24/T-26/T-34/T-37/T-39/T-40) |
| SC-14 | M3 | Recovery workflow substates including `SHORTFALL_HOLD` and operator resolutions (§7.4.1), durable intent, retry ambiguity and fault-injection suite (T-25, T-28) |
| SC-15 | M3 | Capital-cycle closure, new-cycle authorization and lifetime limits: policy decided in PRD v1.4 §5.2.1, mechanics in §7.1.1; transfer key separation and direction restriction (§10) (T-31/T-32) |

A contract is not considered resolved merely by selecting a value: it requires rationale, versioned configuration, and tests/evidence appropriate to its gate.

### 13.1 M1 OFFLINE resolutions

**SC-01.** The manifest pins source assets by SHA-256 and encodes UTC millisecond coverage, ingest time, and modeled publication lags. A bar is half-open and its completed contents enter a decision after its close and declared lag. A replay may use the opening print at its modeled boundary. Metadata requires both effective time and observation time at or before the decision. Captured filters apply from receipt and expire at the policy freshness bound. Symbol plus onboard instant identifies an epoch. A historical sample can use a disclosed first-observed anchor when onboard evidence is unavailable. An observed relisting requires a distinct prior epoch boundary. Contract size encoded in a symbol is preserved for identity and reporting. USD-M candle/fill prices and quantities are already in the exchange's quoted unit.

Funding accounting uses the recorded timestamp and rate. Decision-time forecasts use `premiumIndex` for a verified next settlement and `fundingInfo` for published adjustments. Absence withdraws the adjusted observation. Use the latest two available events to infer spacing within the versioned 5 s jitter bound. The recognized inference candidates are 1h, 2h, 4h and 8h. A supporting archive interval field can corroborate a change. A longer spacing without corroboration after a shorter known one is treated as a possible gap. A documented scenario interval must have explicit source and applicability. The baseline has no default interval. A missing expected record or insufficient first-interval evidence fails continuity without interpolation. Mark klines are preferred. A proxy requires a time-bounded gap record known by the observation boundary and explicit opt-in. The sample used no proxies. [Coverage and provenance](evidence/m1/feasibility.md).

**SC-02.** The single-epoch broker consumes versioned simulated intents and an injected horizon. At a shared timestamp, finish the previous bar's bounded intrabar exit, close snapshot and immediately available trailing ratchet. Then process the next open's boundary funding (prior holder, or the ownership rule below), liquidation/stop gaps, reduce-only exits, deferred trailing updates, and authorized entries. Delayed trailing uses the completed source close and first modeled open at or after all source and intent observations. Keep the old stop through the gap check. A deferred update can ratchet only the same position and only if the new stop is still valid at that opening price. This preserves bar-boundary execution without assuming a price path around the data's arrival time.

Binance documents a 15-second deviation in the actual funding time. Policy `m1-v3` treats a settlement within `funding_ownership_window_ms` (15 s) of a bar boundary as settling at that boundary. The event keeps the recorded time as `SETTLED_AT` and uses the settlement mark, else the opening mark print. Its holder is ambiguous across every modeled fill whose possible execution instants fall within the window. An intrabar protective exit may have executed at any instant of its bar. A settlement inside a bar is ambiguous against a possible protective exit in that bar. An ambiguous settlement posts at the most adverse cash flow among the possible holders, flat included, and is flagged `OWNERSHIP_AMBIGUOUS`. Adverse funding is charged and favorable funding omitted. A later fill in the window can only add an `OWNERSHIP_ADJUSTMENT` debit. Until the bar's OHLC fixes every holder (an intrabar exit, a fill in the window), only min(cash, 0) posts at the settlement and any credit is `CREDIT_WITHHELD`. A credit no holder could forfeit is `CREDIT_RELEASED` at bar end, observable with the bar's close. A published snapshot therefore never depends on prices after its settlement. Adverse funding reduces isolated margin before the protection check. Ownership ambiguity alone never fails a replay. A zero window keeps the exact-time priority: the prior holder pays a settlement at a boundary. An intrabar settlement needs its recorded mark. A hash-pinned REST enrichment can supply it only when the instrument epoch, literal timestamp and rate match the archive event. Decision-time funding estimates remain causal. Stop/liquidation combinations give liquidation priority. A missing candle, due settlement or intrabar settlement mark, an unresolved effective listing boundary, or an unavailable applicable mark or tier while exposed fails the replay. [Binance funding timing](https://www.binance.com/en/support/faq/detail/360033525031).

The entry/exit fill models use an adverse tick and configurable taker fee and slippage. Immediate partial fill is a deterministic order fraction with revalidated quantity and a canceled remainder. Gap exits use stressed slippage from the opening print. Intrabar contract-triggered stops use the stop level with stressed slippage. Mark-triggered stops and liquidation use the adverse contract bound and stressed slippage. Liquidation additionally charges the modeled liquidation fee and blocks further entries. It never returns isolated margin: the cash loss is at least the position's isolated margin plus funding credited while exposed, and any residual is posted once as a `LIQUIDATION_CLEARANCE` fee. Maintenance survival is evaluated on the mark path using isolated allocated margin, the applicable notional bracket, deduction and liquidation-cost allowance. Adverse funding reduces isolated protection margin while posting the cash debit once. Reduction realizes PnL against weighted fill basis and releases proportional collateral. The model does not assume insurance payouts. The stop price basis is explicit in `config/m1.json`. The M1 scenario uses `CONTRACT_PRICE` and tests also cover `MARK_PRICE`. Any planned live setting must match the researched basis and pass SC-13.

Emit an event-numbered wallet/equity snapshot after each fill, funding posting or protective exit and at each completed bar close. Use the mark opening print at open, settlement mark at intrabar funding, and mark close at close. When enabled, contract proxies carry explicit open/close source labels. Use 40-digit Decimal arithmetic with half-even rounding for accounting and a 0.00000001 USDT reconciliation tolerance. Available collateral reserves margin and costs without deducting them from mark equity. Cycle equity deducts modeled closing costs. In `m1-replay-v2`, a snapshot's `timestamp` is its event time and `trigger_available_at` is the earliest decision time at which the valuation and its funding postings are available. The latter is null for valuations based on intrabar OHLC bounds or missing data. The `trigger_observable` flag means availability at the event time. A delayed funding posting blocks sizing until it is observable. M2 still owns which eligible observations evaluate capital rules under SC-04/SC-05. [Checks and evidence](evidence/m1/verification.md).

**SC-12.** Apply §5.2 with a common step for both lot filters and validate minimum notional against the conservative entry/mark basis. Compute the margin cap using collateral net of opening excess and a fixed plus per-unit reserve, solved algebraically to account for quantity-dependent fees and funding. Bound quantity across the supplied maintenance tiers using isolated solvency at the stressed stop plus buffer and at the current mark. Round stop prices toward the actual fill and sizes down, then revalidate. `PRICE_FILTER` bounds entry and stop triggers. `PERCENT_PRICE` bounds limit prices. SC-13 still verifies the conditional-order family. Store the four cap values, binding cap, usage, evidence and all applicable reasons. The 50 USDT report samples the PRD risk ranges using endpoints and midpoint, the published candidate leverage ceilings and representative Growth stakes of 100 and 250 USDT. Cost and stop factors are reproducible scenarios. They set no research promotion threshold. Report schema `m1-feasibility-v4` keeps each attempt's input `provenance` and adds `assessable`, which is false when a critical input is unavailable, stale or disallowed. It uses `INCONCLUSIVE_NO_ASSESSABLE_ATTEMPTS` for zero attempts and `INCONCLUSIVE_CRITICAL_INPUT_UNAVAILABLE` when no attempt is assessable. `NO_TRADE_IN_DECLARED_WINDOW` requires at least one assessable rejection. [M1 policy](config/m1.json) · [50 USDT report](evidence/m1/feasibility.md).

### v0.5 targeted change notes

- Recorded M1 technical resolutions for SC-01, SC-02 and SC-12 with versioned OFFLINE assumptions and regression evidence.
- Independent review addressed liquidation clearance, effective listing boundaries, stop filters and feasibility assessment. Follow-up versioned replay availability and trailing, settlement mark evidence and empty feasibility windows. Policy `m1-v3` replaces the failing funding ownership guard with a boundary settlement window and an adverse-candidate posting rule. Feasibility schema v4 separates provenance from assessability, and the delayed-trailing rule is noted for M2 (§4.5.1). [Review and verification](evidence/m1/verification.md).

## 14. Change control and release policy

- PRD controls **what and why**; SPEC controls **precise behavior and invariants**; ROADMAP controls **when and acceptance**; README controls **setup and operation**.
- Changes to capital baselines or product scope require PRD review. Technical choices may be resolved here with short rationale and test references; no mandatory separate CANON/ADR/audit pack.
- Workflow: **Define → Implement → Test → Review → Fix → Merge**.
- A failed M2 gate may stop the project. No automatic promotion, no repeated holdout tuning, and no live trading until M5 explicitly authorized.

## 15. v0.4 targeted change notes

- Clarified that PAPER can start on public data alone (T-34), funding estimates use decision-time evidence and explicit default scenarios (T-42), and the exact operational reserve requires promotion evidence under the M2 criteria (§7.3.1).
- Funding evidence (§4.3.1): settlement events `VERIFIED`; derived intervals `RECONSTRUCTED`; gaps `UNAVAILABLE`; no 8h default; `premiumIndex` added to forward capture; T-42.
- M1/M2 boundary (§4.5.1): M1 owns simulator event processing; M2 owns strategy semantics and trigger cadence; M1 rules are not tuned on M2 results.
- Recovery reserve scenarios preregistered before the M2 holdout (0 USDT baseline plus a cost-model-error scenario, one primary); dead-zone rate reported; the exact operational reserve must pass the preregistered M2 criteria, so exact candidates are preregistered (§7.3.1).
- `RESUME_IGNITION` requires `cycle_equity < 100`; the dead zone stays in `SHORTFALL_HOLD`; T-43.
- Editorial: product renamed from "ACGS V2" to **ACGS** (PRD v1.4). No behavior changed.
- Defined `cycle_equity` (conservative net liquidation value excluding external inflows) as the measure for the 100 USDT trigger and 30 USDT halt, with cadence parity and a required M2 recovery shortfall-rate metric (§7.3.1). Thresholds unchanged.
- Added `SHORTFALL_HOLD` and the only permitted operator resolutions after a recovery shortfall (§7.4.1); clarified INV-04.
- Added account isolation, account preconditions and external cash-flow handling (§7.6, INV-11).
- Added the halt-headroom risk cap and `HALT_HEADROOM` reason code (§8.1).
- Added T-27–T-30 and SC-15.
- Aligned with PRD v1.4: capital-cycle records and lifetime limits (§7.1.1), transfer key separation and Futures→Spot-only adapter (§10), mainnet shadow `PAPER` and staged `LIVE` (`LIVE_SHADOW` via test-order endpoint → smoke test → `LIVE_ARMED`), no testnet (§3.1, INV-08, T-34, T-39).
- Added capability-boundary enforcement (§2.1) and risk base, distance-preserving initial stop, tick rounding toward entry and binding-constraint diagnostic (§5.2, INV-07); SC-12 report now covers Growth risk levels.
- Data: instrument epochs, point-in-time funding intervals, forward capture, mark-price kline coverage, liquidation versus stop price basis (§4.2/§4.3/§4.5).
- Strategy: step-wise ATR trailing implementation decided; universe-exit handling added to the open list (§6). Research: random-entry and ablation baselines, calendar-block resampling, lifetime-limit success probability, post-mortem tolerance (§11).
- Added T-31–T-38. PRD v1.4 baselines preserved.
- Hardened `RESUME_IGNITION` with automated cost-model, risk-digest and headroom preconditions and a limit of two shortfalls per cycle (§7.4.1).
- Added INV-12 (smoke test is the hard gate for protective orders; test-order acceptance is never evidence) and the conditional/algo endpoint family requirements (§9.2, T-40).
- Sizing takes the most restrictive of risk, margin, liquidation and limit caps, quantizes down, then validates all filters; orders below 100% risk utilization are valid (§5.2, T-41). First-cycle authorization needs no previous-cycle closure or post-mortem (§7.1.1).
- Opening excess of up to 0.50 USDT is excluded from `cycle_equity` and from available collateral for sizing/margin, and is never trading capital (§7.1.1).

## Appendix — v0.3 targeted change notes

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
