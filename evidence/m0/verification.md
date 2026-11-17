# M0 verification

Status: every M0 acceptance check passes at code commit `e16d08f` except one. A live forward capture with the stricter record validation must still be run from the operator's machine (see the table). Milestone acceptance is pending operator review. The baseline is `main` at `53992e0`.

## M0 acceptance summary (code commit `e16d08f`)

| ROADMAP §3 criterion | Evidence | Result |
|---|---|---|
| Clean install and replay smoke test | CI `native` (Ubuntu 24.04, Windows 2025) and `container` jobs: locked install, smoke twice | Passed ([push run 44191219078](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44191219078), [PR run 44191231342](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44191231342)) |
| CI green | All four hosted jobs | Passed on both runs |
| Secret scan clean | CI `secrets` job: Gitleaks v8.24.3 by digest, full history | No leaks |
| Invalid config fails safely | `tests/test_foundation.py` | Passed |
| No real exchange order or transfer path | `tests/test_import_contracts.py` (T-16, INV-08) | Passed |
| Restart preserves the state fixture | Smoke replay verification, v1 to v2 migration test | Passed |
| Import contracts enforced in CI (T-13) | `tests/test_import_contracts.py` in both native jobs and the container `check` service | Passed; 118 tests in total |
| Eligibility recorded | [Account eligibility record](account-eligibility.md) | Every item recorded as `UNVERIFIED`, which M0 allows. The jurisdiction item must be verified before M3. |
| Forward capture stores provenance-tagged snapshots | Fixture tests at `e16d08f`. Live run with schema v2 on the Windows workstation at `22f01d6` | **Live re-run pending.** `e16d08f` tightened record validation, so one live `capture` from the operator's machine must confirm five `CAPTURED`/`VERIFIED` responses. The cloud agent's IP receives HTTP 451 from Binance (restricted location); the latch stopped capture after the first request, as designed. |
| Reproducible on a clean environment | Fresh hosted runners | Passed |

## Wall-clock import contract verification

- Code commit `e16d08ff5ceeead9906db2d83cea3f797b8e7f28` validates each capture record's structure and minimum field types and values before tagging it `VERIFIED`. Malformed bodies become `INVALID_PAYLOAD` and are retried after 60 seconds. In the cloud agent (Ubuntu 24.04), `uv run --locked ruff check .`, `uv run --locked mypy` and `uv run --locked pytest -q` passed with 118 tests. All four hosted jobs passed on the [push run 44191219078](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44191219078) and the [PR run 44191231342](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44191231342).
- Code commit `460aed63ff2473162b2d60c953e57e03faad812b` scopes the injected-clock exemption to the function (or `self` field) where `Clock` is declared, removes it when a name is rebound or shadowed, and resolves `Clock` through import aliases and string annotations. In the cloud agent (Ubuntu 24.04), `uv run --locked ruff check .`, `uv run --locked mypy` and `uv run --locked pytest -q` passed with 92 tests. All four hosted jobs passed on the [push run 44189209005](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44189209005) and the [PR run 44189220716](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44189220716).
- Code commit `c38cd0919096700a338cbddaf8c84a5f7efcc625` rejects `now`/`utcnow`/`today` on receivers other than the injected clock and argument-less `astimezone()`, and stops dependency traversal at capability modules. In the cloud agent, ruff, mypy and pytest passed with 81 tests. All four hosted jobs passed on the [push run 44188014146](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44188014146) and the [PR run 44188025872](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44188025872).
- Code commit `df8f9a1d1353bf93db2baaf36e3c0a3eb134e728` closes the re-exported wall clock and helper I/O gaps. Tests cover direct and aliased clock re-exports, transitive file and SQLite access, and allowed imports of pure config and ledger types. On Windows native, `uv run --locked ruff check .`, `uv run --locked mypy`, and `uv run --locked pytest -q` passed with 77 tests. All four hosted jobs passed on the [push run 44126484575](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44126484575) and the [PR run 44126503728](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44126503728).
- At `fc20e77`, 68 tests passed locally. All four hosted jobs passed on the [push run 44123400742](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44123400742) and the [PR run 44123414721](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44123414721).
- At `9eee5c9`, 66 tests passed locally. All four hosted jobs passed on the [push run](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44121775884) and the [PR run](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44121791094).

## Re-verification after review fixes (`6010a75`)

The review fixes change capture storage (schema v2: zlib payloads, a persistent HTTP 403/451 latch), timestamps, smoke replay, the import contract, Docker targets and CI pinning. The earlier results further down predate them.

| Check | Command or evidence | Result |
|---|---|---|
| Locked install, lint, types, tests on Linux | `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked mypy`, `uv run --locked pytest` in the cloud agent (Ubuntu 24.04) | Passed: 63 tests |
| Offline smoke and restart on Linux | `uv run --locked python -m acgs --db <tmp> smoke` twice | Recorded, then replayed and verified with the same digest |
| Hosted CI | [M0 checks run 44119751994](https://github.com/soryusherlockmaiden/verbose-couscous/actions/runs/44119751994) at `6010a75` | All four jobs passed (details in the rows below) |
| Native Ubuntu 24.04 and Windows 2025 | CI `native` jobs: locked install, ruff, mypy, pytest, smoke twice | Passed on both runners |
| Docker build, smoke and in-container checks | CI `container` job: `docker compose build acgs`, `docker compose run --rm acgs smoke` twice, `docker compose --profile check build check`, `docker compose --profile check run --rm check` | Passed |
| Full-history secret scan | CI `secrets` job: Gitleaks v8.24.3 pinned by digest, `git --redact --log-opts=--all` | No leaks |
| Live forward capture with schema v2 | `uv run --locked python -m acgs --config config/m0.toml --db data/m0-schema2-review.sqlite3 capture` on the Windows workstation | Five HTTP 200 responses tagged `VERIFIED`. Verified stored schema version 2, zlib decompression and raw SHA-256 for each response. |

The Python digest came from the existing Docker Desktop image's `RepoDigests` and matches the digest resolved by the earlier build. With the pinned reference, `docker compose --profile check build acgs check` built both targets. `docker compose run --rm acgs smoke` replayed the fixture from the existing v1 ledger, and `docker compose --profile check run --rm check` passed lint, types and 63 tests. Native Windows lint, types and 63 tests also passed.

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
