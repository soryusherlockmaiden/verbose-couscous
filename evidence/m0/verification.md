# M0 verification

Status: **re-verification pending** after the PR #2 review fixes. Milestone acceptance is pending operator review. The baseline is `main` at `53992e0`.

## Re-verification after review fixes

The review fixes change capture storage (schema v2: zlib payloads, a persistent HTTP 403/451 latch), timestamps, smoke replay, the import contract, Docker targets and CI pinning. The earlier results below predate them.

| Check | Result for the review-fix commit |
|---|---|
| Locked install, `ruff check .`, `mypy`, `pytest` on Linux (cloud agent, Ubuntu 24.04) | Passed: 63 tests |
| Offline smoke twice on Linux | Recorded, then replayed with the same digest |
| Native Windows checks | **Not yet run** |
| `docker compose build`, smoke, `check` service | **Not yet run** (no Docker daemon in the cloud agent); hosted CI runs them |
| Full-history secret scan | **Not yet run** locally; hosted CI runs it |
| Live forward capture with schema v2 | **Not yet run** |
| Hosted CI for the review-fix commit | **Pending** |

## Earlier verification (commits `0134e89`/`da12ab8`, before the review fixes)

| ROADMAP §3 gate | Command or evidence | Result |
|---|---|---|
| Clean locked install | `uv python install`, `uv sync --locked` | Python 3.12.13 and uv 0.13.7. Locked install succeeded on native Windows and in a clean Linux container. |
| Lint, types, tests | `uv run --locked ruff check .`, `uv run --locked mypy`, `uv run --locked pytest` | Windows and Linux container passed. Each ran 23 tests. |
| Offline smoke and restart | `uv run --locked python -m acgs --db data/m0-smoke.sqlite3 smoke` twice and `docker compose run --rm acgs smoke` twice | Both recorded then replayed the same state fixture and digest. Compose used a named volume. |
| Docker build | `docker compose config --quiet`, `docker compose build acgs` | Passed on Docker Desktop. |
| Invalid config, T-13 and T-16 | `pytest tests/test_foundation.py tests/test_import_contracts.py` (part of the suite) | Unsupported modes and invalid config rejected before DB or network access. Import contracts check direct and indirect access to disallowed layers. M0 exposes a public GET client and the offline smoke entry point. |
| Secret scan | README Gitleaks command with `git --redact --log-opts=--all /repo` | No leaks in the locally scanned history. CI repeats the scan after checkout with full history. |
| Eligibility | [Account eligibility record](account-eligibility.md) | `UNVERIFIED` pending operator evidence. |
| Forward capture | `uv run --locked python -m acgs --config config/m0.toml --db data/m0-capture.sqlite3 capture`, `docker compose run --rm acgs capture`, and the Compose `capture` profile | Both one-shot runs captured five public responses with HTTP 200 and `VERIFIED` observation provenance. The scheduled service captured five responses before it was stopped. |
| Hosted CI | [PR #2 · M0 checks](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44116856769) at `0134e89` | All four jobs passed: native Ubuntu 24.04, native Windows 2025, Docker Compose, and the full-history secret scan. |

The Windows capture completed on 2026-11-17 at 15:12 UTC. Responses covered `exchangeInfo`, `fundingInfo`, `premiumIndex` and order-book depth for BTCUSDT and ETHUSDT. The raw `exchangeInfo` response had SHA-256 `8752c0b044f001e1d136f0a3ce87459c1a152dd4e9e40f05da19f2c15573f635`. Raw data are in the ignored local SQLite database. Test JSON files are excerpts from these public responses, so their hashes differ from the live responses. Since the review fixes, the suite pins each fixture file's SHA-256 and checks that captured digests match them, along with request and receive timestamps, source URLs, idempotency, failed-response handling, persisted rate-limit backoff and the HTTP 403/451 latch.
