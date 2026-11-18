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
