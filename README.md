# ACGS — Adaptive Capital Growth System

ACGS is a research-first, safety-first experiment with automated trading on **Binance USD-M USDT perpetual futures**, starting from **50 USDT**. It tries to grow the stake to a 100 USDT trigger, recover the original 50 USDT to Spot, and then compound only the remaining active capital at lower risk.

> **Risk warning:** Futures trading can lose the entire stake, including losses beyond modeled stops (gaps, liquidation, slippage, API faults). Nothing in this repository is financial advice or a promise of returns. The operator is responsible for account eligibility and legal compliance in their jurisdiction.

## Status

| Item | State |
|---|---|
| Phase | **M0 Foundation**: technical verification passed, including live capture; operator acceptance pending |
| `PRD.md` | v1.4, Approved & Locked |
| `SPEC.md` | v0.4, engineering baseline candidate |
| `ROADMAP.md` | v0.4, progress and evidence in §12 |
| Live orders / real transfers | **Not authorized** |

## Core documents

ACGS maintains four authoritative core documents. Milestone-specific research artifacts, configurations, evidence reports and operational runbooks are permitted when necessary. They are not additional governance documents or independent sources of product truth.

| File | Responsibility |
|---|---|
| [`README.md`](README.md) | Project overview, setup, commands and operating modes |
| [`PRD.md`](PRD.md) | Product intent, scope, outcomes and acceptance requirements (what and why) |
| [`SPEC.md`](SPEC.md) | Executable behavior, formulas, contracts and invariants (exactly how) |
| [`ROADMAP.md`](ROADMAP.md) | Milestones, sequencing, acceptance and progress (when) |

This README summarizes the other documents but never overrides them. If anything here disagrees with PRD, SPEC or ROADMAP, those documents win, and this README should be corrected.

## How it is meant to work

### Capital lifecycle (PRD §5, SPEC §7)

| Phase | Summary |
|---|---|
| `IGNITION` | Starts from a fixed 50 USDT reference. Fixed-loss halt when `cycle_equity <= 30` USDT. This is a 40% loss from initial capital, **not** a peak drawdown, and **not** a guaranteed floor. |
| `RECOVERY_PENDING` | Triggered when `cycle_equity >= 100` USDT. Entries freeze and positions are flattened and reconciled. Exactly 50 USDT moves Futures → Spot only if at least 50 USDT plus reserve remains active. A shortfall stays pending under strict, automated resume rules. |
| `GROWTH` | Lower-risk compounding of the remaining active capital, entered only after a confirmed recovery. Never returns automatically to Ignition. |
| `VAULT` | Entered at 500 USDT active Futures equity. Capital preservation; optional profit locking is research-pending. |

- `cycle_equity` is conservative net liquidation value. It excludes estimated closing costs, unapproved external inflows and any opening excess (SPEC §7.3.1).
- A failed cycle closes permanently. **Lifetime limits: at most two Ignition cycles and 100 USDT total committed capital** (PRD §5.2.1).
- Risk-per-trade ranges, leverage ceilings and Growth/Vault drawdown limits are **research candidates**, not approved live values.

### Strategy and research (PRD §6, SPEC §5–§6, §11)

- The only MVP candidate is **Volatility Breakout with trend/volume filters**. It is a hypothesis with no claimed edge.
- Universe: point-in-time *Major + Dynamic Top Movers*, delisting-aware, with every input provenance-tagged (`VERIFIED` / `RECONSTRUCTED` / `UNAVAILABLE`).
- Every candidate order must be executable at 50 USDT under real exchange filters. Rounding never increases risk, and an infeasible symbol is skipped.
- Numeric promotion gates are preregistered before the untouched holdout is opened. `PASS`, `FAIL` and `INCONCLUSIVE` are all valid outcomes, and **no-trade** is valid.
- Stopping after M2 (no edge) or after M4 (paper only) counts as a successful outcome. A single live cycle (~15 trades) does not validate an edge (PRD §12.4).

## Operating modes (SPEC §3)

A process runs in exactly one mode, fixed at startup. Changing mode requires a restart and explicit reauthorization. **No exchange testnet is used.**

| Mode | Data | Orders | API key | Milestone |
|---|---|---|---|---|
| `OFFLINE` (default) | Immutable historical snapshots | Deterministic simulation | None | M1–M2 |
| `PAPER` (mainnet shadow) | Binance mainnet live feeds; optional real-account reads | Built exactly as live, recorded as `SHADOW_ORDER`, **never sent**; fills simulated | Optional **read-only** key; trading-capable keys are refused | M4 |
| `LIVE_SHADOW` | Live account | Sent **only** to the exchange test-order endpoint; fills simulated; no real transfers | Trading key | M5, stage 1 |
| Protective-order smoke test | Live account | A few real minimum-size orders, approved for this test only | Trading key | M5, stage 2 |
| `LIVE_ARMED` | Live account | Real orders under the approved live configuration | Trading key, plus a separate transfer key | M5, stage 3 |

Test-order acceptance proves only that a request is valid. A protective-order type may be used in `LIVE_ARMED` only after passing the real smoke test under the current account configuration (SPEC INV-12).

## Safety principles

- **Strategies cannot trade.** Only the execution adapter submits orders and only the transfer adapter moves funds. This is enforced by CI import contracts (SPEC INV-01, §2.1).
- **Fail-closed.** Stale data, unknown orders, unprotected positions or reconciliation mismatches block new entries. A halt is latched and is never cleared by a restart, reconnect or config reload.
- **Exactly-once recovery.** Recovery intent is stored durably before any action. Retries are never blind, and nothing counts as a completed transfer without independent exchange evidence.
- **Least-privilege keys.** No withdrawal permission. Trading and transfer keys are separate where supported. The transfer adapter is restricted in code to Futures → Spot, and ACGS never moves Spot → Futures.
- **Dedicated account.** The bot uses its own account or sub-account in single-asset mode with BNB fee deduction off. External cash flows pause the bot and are excluded from equity triggers (SPEC §7.6).
- **Secrets** are never committed, logged or placed in fixtures.

## Milestones (ROADMAP)

| Milestone | Goal | Status |
|---|---|---|
| M0 Foundation | Offline skeleton, CI, config, account eligibility pre-check, forward data capture | Technical PASS; awaiting operator acceptance |
| M1 Market & Offline Execution | PIT data, deterministic simulator and equity ledger, 50 USDT feasibility report | Not started |
| M2 Strategy Research | Preregistered research decision: `PASS` / `FAIL` / `INCONCLUSIVE` | Not started |
| M3 Capital & Risk Engine | State machine, risk engine, recovery simulation, fault injection | Not started |
| M4 Paper Pilot | Mainnet shadow operation with no real orders | Not started |
| M5 Controlled Live Pilot | Optional; staged `LIVE_SHADOW` → smoke test → `LIVE_ARMED` | Not authorized |

M0–M4 never place real orders or real transfers.

## Setup and commands

### Windows native (PowerShell)

Run in the Windows checkout. Install Git and uv 0.13.7 first. uv reads the Python 3.12.13 pin from `.python-version` and creates a local `.venv`.

```powershell
Set-Location C:\dev\acgs
uv python install
uv sync --locked
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked python -m acgs smoke
uv run --locked python -m acgs smoke
```

The second smoke run replays the same state fixture in `data/acgs.sqlite3`. On Ubuntu 24.04 or a Linux cloud agent, run the same uv commands from the checkout. Each checkout has its own virtual environment and database. CI runs these checks on `ubuntu-24.04` and `windows-2025`.

### WSL2 and Linux (Docker Compose)

On Windows, use a WSL2 distro with Docker Desktop's WSL integration enabled. Run this from the WSL terminal. Keep the checkout under the Linux home directory. On Linux, run the same commands with Docker Engine and the Compose plugin.

```sh
cd ~
git clone https://github.com/soryusherlockmaiden/verbose-couscous.git acgs
cd acgs
docker compose build acgs
docker compose run --rm acgs smoke
docker compose run --rm acgs smoke
docker compose --profile check build check
docker compose --profile check run --rm check
```

Compose stores SQLite in the `acgs-data` named volume. The smoke job records a deterministic state fixture, and on replay it verifies that the stored record still matches; a changed record exits with an error. Historical market replay arrives in M1. The `check` service runs the same ruff, mypy and pytest checks as the native workflow inside the container.

### Public forward capture

Defaults are in [config/m0.toml](config/m0.toml). M0 supports `OFFLINE` only. A missing mode defaults to `OFFLINE`; an invalid or unsupported mode is refused at startup. Pass `--config` and `--db` before the subcommand when running the Python entry point.

```text
uv run --locked python -m acgs --config config/m0.toml capture
uv run --locked python -m acgs --config config/m0.toml capture --watch
uv run --locked python -m acgs --config config/m0.toml capture --clear-restriction
docker compose run --rm acgs capture
docker compose run --rm acgs capture --clear-restriction
docker compose --profile capture up -d capture
docker compose --profile capture logs -f capture
docker compose --profile capture stop capture
```

Use one capture process per database. The scheduled service polls for due work. It records `exchangeInfo` and `fundingInfo` once per UTC day, and `premiumIndex` plus shallow order books at the configured interval (default five minutes, BTCUSDT and ETHUSDT for depth). Successful records carry the response (stored zlib-compressed), fixed-width UTC timestamps, source URL, the SHA-256 of the raw response bytes and observation provenance.

A HTTP 200 response counts as captured only if every record passes minimum structure and value checks; otherwise it is stored as `INVALID_PAYLOAD` without provenance and retried after 60 seconds.

HTTP 403/451 stops the capture process and latches: later runs refuse to send any request until the operator has reviewed the cause and runs `capture --clear-restriction`, which is recorded as an audit event. Rate-limit responses defer further requests. A one-shot `capture` exits 0 only when everything due was captured.

The all-symbol `premiumIndex` snapshot dominates storage at the default five-minute interval (roughly 150 KB per snapshot before compression). Check the database size during the first days of operation and keep enough free disk on the VPS. Capture uses only public market-data GET requests and requires no API credentials.

To scan the full Git history from a checkout with Docker, run the same Gitleaks image as CI:

```text
docker run --rm --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo --mount "type=bind,source=${PWD},target=/repo,readonly" ghcr.io/gitleaks/gitleaks:v8.24.3@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055 git --redact --log-opts=--all /repo
```

## Repository layout

```text
README.md     Overview, operating modes, setup (this file)
PRD.md        Product requirements (Approved & Locked)
SPEC.md       System specification
ROADMAP.md    Milestones and acceptance
src/acgs/     Offline configuration, SQLite ledger, and public capture
config/       M0 configuration
tests/        Offline safety checks and public data fixtures
evidence/m0/  Verification and manual eligibility record
AGENTS.md     Instructions for AI coding agents (not a product document)
CLAUDE.md     Claude Code pointer to AGENTS.md
```

## Workflow and change control

- Work follows **Define → Implement → Test → Review → Fix → Merge**, with small changes and acceptance tests attached.
- Changes to capital baselines or product scope require PRD review.
- Technical decisions go in SPEC with a short rationale and test references. Progress goes in ROADMAP.
