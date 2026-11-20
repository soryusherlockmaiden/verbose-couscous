# M2 O2 step 1 metadata inventory

The operator saved all five publications as PDFs from the browser. Archive inventory and checksum matching are complete for `BTCUSDT`, `ETHUSDT` and `DGBUSDT` over `[2023-01-01T00:00:00Z, 2025-01-01T00:00:00Z)`. [Notice evidence](o2-notices.json) · [JSON matrix](o2-inventory.json) · [O2 decision](protocol.md#operator-decisions)

## Archive coverage

| Symbol | Monthly rows | `FOUND` | `NOT_FOUND` | `UNVERIFIED` | `OUTSIDE_ACTIVE_PERIOD` |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 168 | 168 | 0 | 0 | 0 |
| ETHUSDT | 168 | 168 | 0 | 0 | 0 |
| DGBUSDT | 168 | 112 | 0 | 0 | 56 |
| Total | 504 | 448 | 0 | 0 | 56 |

Each month has seven series covering `klines` and `markPriceKlines` at `15m`, `1h` and `1d`, plus `fundingRate`. The delisting boundary recorded in M1 places May–December 2024 outside the DGBUSDT active period. The inventory skipped requests for those months and excluded them from the gap count. No month in the window could be classified as preceding a listing. [M1 evidence](../m1/feasibility.md)

April 2024 contains the DGBUSDT delisting boundary. All seven monthly keys were found. On April 1, all six daily candle bundles were found and the daily `fundingRate` key returned HTTP `404`. The monthly funding bundle exists. Checking for the final settlement requires an approved payload. No other missing monthly keys triggered daily probes.

All **454** ZIPs found had a size in the response header and a CHECKSUM with a SHA-256 and matching filename. For identical URLs, **33/33** public checksums matched their M1 pins. The M1 sample contains `1d` bars in `monthly` bundles and monthly funding for BTCUSDT and ETHUSDT in January–April 2024 and DGBUSDT in January–March. Sizes and checksums were observed during metadata collection. Candle continuity, funding continuity and point-in-time universe membership require evidence beyond bundle presence. [M1 manifest](../m1/binance-2024.json) · [M1 coverage](../m1/feasibility.md)

## Notices and evidence gaps

Discovery searches used date limits and keywords for listings, status changes, delistings, filter and tier updates, and contract or symbol changes including multipliers. Searches for older listings also covered 2019–2022. The discovery log records the provider, all five original query batches, screening criteria and the five official publication URLs selected. Out-of-period results were ignored. The original run did not save the full results pages or a list of each discarded result. All selected URLs returned HTTP `202` with `Content-Length: 0`, leaving no page content to save or hash. The matrix records `BLOCKED_EMPTY_HTTP_202`, the observation time and the empty-body hash. Index results served only to find URLs. The retained DGBUSDT status and tier evidence came from the accepted M1 manifest, with its source recorded separately. [Discovery log](o2-discovery.json)

| Evidence type | Findings and gaps |
|---|---|
| Listing, status and epoch | The blog cites September 9, 2019 as the BTCUSDT launch date. The ETHUSDT blog says trading started on the publication day, and the browser tab and PDF display different dates. The DGBUSDT notice bars new positions from April 1, 2024 at 08:30 UTC, schedules settlement at 09:00 UTC and states that delisting follows settlement. Exact onboard times, status changes across the period and multiplier relabeling remain incompletely documented for the three symbols. |
| Filters | Evidence for point-in-time USD-M step sizes, quantity and notional minima, and tick sizes in 2023–2024 is insufficient to assess execution. Search results for spot or delivery contracts were excluded from perpetual evidence. |
| Maintenance tiers | Operator copies record post-change caps, rates and leverage for DGBUSDT and two BTCUSDT/ETHUSDT schedules. The published tables omit deduction. The January notice reports a December effective date for BTC/ETH, before publication. The May notice says the changes would take about 30 minutes to complete. Follow publication times and the stated scope when applying the evidence. Period coverage and position-level applicability still require review under O3. |
| Spread | The inventory found no retrospective spread evidence in the M1 dataset it reviewed. Candle and funding series contain no spread measurements. |

### Wayback and operator copies

| Publication | Wayback CDX | Operator copy |
|---|---|---|
| DGBUSDT delisting and tiers | `NOT_ARCHIVED` | PDF reviewed |
| BTCUSDT launch history | `NOT_ARCHIVED` | PDF reviewed |
| ETHUSDT launch | `NOT_ARCHIVED` | PDF reviewed |
| BTCUSDT/ETHUSDT tiers, January notice | `NOT_ARCHIVED` | PDF reviewed |
| BTCUSDT/ETHUSDT tiers, May change | `NOT_ARCHIVED` | PDF reviewed |

CDX queries used exact original URLs, capture status `200` and capture cutoff `20241231235959`. All five HTTP `200` responses returned empty arrays. The archive status is scoped to those queries. One Python attempt failed TLS verification before receiving an HTTP response. Five HTTP queries completed with curl using Schannel, without a proxy or redirects. The built-in Codex browser then opened the five official URLs at the operator's request. Automatic browser subrequests were uncounted. The operator printed all five to PDF and the copies were stored in `data/o2-inventory/notices-operator/`. The hashes identify the saved PDF bytes. Save times were taken from the PDF `CreationDate` fields and converted to UTC. [Query URLs, hashes and facts](o2-notices.json)

For DGBUSDT, the close-only time, scheduled settlement, tier update time and lowest bracket (5,000 USDT cap, 2% rate, 11× maximum leverage) match the M1 record. The PDF displays March 25 at 04:40 without a time-zone label. The browser tab displays 11:40. The notice omits a separate completion time for settlement or delisting and omits deduction. Record these limitations before using the evidence for eligibility. [M1 comparison](../m1/feasibility.md) · [Structured cross-check](o2-notices.json)

### Registering operator copies

Save the page from a regular browser as a PDF or HTML file in `data/o2-inventory/notices-operator/`. Keep the URL in the PDF header or footer. Record the UTC save time and transcribe the publication date as displayed. From the checkout root, use the following command for the reviewed DGBUSDT example.

```powershell
.\.venv\Scripts\python.exe evidence/m2/ingest_notices.py --url 'https://www.binance.com/en/support/announcement/detail/ce74360a3fd444b2b35da7186f8dd0ad' --file data/o2-inventory/notices-operator/DGBUSDT-delisting-2024-03-25.pdf --saved-at 2026-11-18T22:18:25Z --published-display '2024-03-25 04:40' --facts data/o2-inventory/notices-operator/DGBUSDT-delisting-2024-03-25.facts.json
```

For new copies, `--facts` is optional. Omitting it marks facts `PENDING_REVIEW`. To complete review, inspect the page and provide a JSON array with `symbol`, `kind` and the transcribed facts. Repeat the import with the same file and metadata. Use `null` for unstated values. The import runs offline. Keep local copies and transcription files outside Git. The ledger records `operator-saved browser copy` provenance and retains the CDX result separately.

## Methods and request counts

| Host | Direct requests | Result |
|---|---:|---|
| `data.binance.vision` | 455 × ZIP `HEAD`, 454 × CHECKSUM `GET` | 908 HTTP `200` responses and one HTTP `404` for the DGBUSDT daily funding key. |
| `www.binance.com` | 5 × page `GET` in the initial run | 5 empty HTTP `202` responses. The follow-up used five browser navigations at the operator's request. |
| `web.archive.org` | 5 × CDX HTTP requests in the follow-up | 5 HTTP `200` responses with empty arrays. One additional attempt failed during TLS verification. |

Discovery used 18 date-limited index queries. The direct connection counts exclude requests internal to the search provider. Archive probes used exact keys constructed from symbol × date × series. Direct connections used no redirects, directory listings, active-symbol lists, ZIP GETs or Range requests. The inventory downloaded **0 bytes** of market payloads.

Git tracks the collection helper and validator. Run both from the checkout root with `acgs` installed in the Python environment. Use a new directory under `data/` for a rerun. It holds the request log, CHECKSUM bytes and raw copies of any pages retrieved successfully. On a rerun, the collector stops contacting the announcement host after the first empty HTTP `202` response. The original run's five attempts remain recorded as evidence.

```powershell
.\.venv\Scripts\python.exe evidence/m2/collect.py archives --work-dir data/o2-inventory-rerun
.\.venv\Scripts\python.exe evidence/m2/collect.py notices --work-dir data/o2-inventory-rerun
.\.venv\Scripts\python.exe evidence/m2/finalize.py --work-dir data/o2-inventory-rerun
```

The offline validator writes the matrix to the run directory. To audit the original run, use `--work-dir data/o2-inventory`, which contains the original log and observations in the checkout used for collection. The validator matches requests to matrix rows, checks that HEAD precedes the CHECKSUM GET, and applies seal rules to bundle coverage. The original run saved SHA-256 values read from CHECKSUM. Reruns also save CHECKSUM bytes for independent verification. Review saved notices for publication time, effective time and relevant facts before using them as research evidence.

The original run used the following repository checks.

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest -q --basetemp=C:\dev\acgs\data\o2-inventory\pytest
docker compose config --quiet
docker compose -p acgs-o2-inventory --profile check build acgs check
docker compose -p acgs-o2-inventory --profile check run --rm check
docker compose -p acgs-o2-inventory run --rm acgs smoke
docker compose -p acgs-o2-inventory run --rm acgs smoke
git diff --check
```

The validator checked 504 unique monthly rows, seven daily keys at the DGBUSDT boundary, bundle coverage against the existing seal rules, request methods, all M1 matches, and equality of the `access` object with base commit `a2c1352`. Ruff, mypy and pytest passed on native Windows and in Linux Docker, with **290 tests** in each environment. Both images built successfully. Two Docker smoke runs each recorded and replayed the same digest, `35be0f1204fa4a70ff517a6643703bfbc657ee64b8cd0827682a183d3db7bc34`.

In the auditability follow-up, `mypy src evidence/m2/collect.py evidence/m2/finalize.py` passed on 28 files. Checks with mock connections verified rejection of ZIP GETs, listings, post-cutoff keys and unlisted notice URLs. They also checked that requests stopped after HTTP `403`, `451` and an empty notice `202`. Offline regeneration preserved all original observations and added references to the discovery log.

In the notice follow-up, all five PDFs were opened with `pypdf`, the footer URLs were checked and the first pages were rendered with `pdftoppm`. SHA-256 and size matched the ledger for all five PDFs. The six notice import cases in `tests/test_ingest_notices.py` are included in the **296 tests** collected by standard pytest. The Docker `check` image includes the import helper under test.

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe src evidence/m2/ingest_notices.py tests/test_ingest_notices.py
.\.venv\Scripts\pytest.exe -q --basetemp=C:\dev\acgs\data\o2-inventory\pytest-notices-ci
docker compose -p acgs-o2-notices-ci --profile check build check
docker compose -p acgs-o2-notices-ci --profile check run --rm check
.\.venv\Scripts\python.exe evidence/m2/finalize.py --work-dir data/o2-inventory --output evidence/m2/o2-inventory.json
git diff --check
```

## Stage B expanded metadata inventory

The operator approved the provisional 340 symbols / 342 epochs on 2026-11-20 UTC for metadata collection over [2023-01-01, 2025-01-01), including planned and all possibly active months. The initial physical plan has 57,064 monthly HEAD keys and at most 57,064 CHECKSUM GETs. Another 56 monthly keys are proven outside the DGBUSDT active period and skipped. Shared BNXUSDT and TLMUSDT keys are collected once and associated with each relevant provisional epoch. Their contents remain unallocated across epochs.

The Stage B entry point extends `collect.py` through `stage_b.py`. It allows direct ZIP HEADs and CHECKSUM GETs up to 2 KB on `data.binance.vision`, preserves the existing 0.08-second pause and 12-second socket timeout, and checks the full source bundle against the registered seal before sending. Responses to HEAD carry no downloaded market payload. The collector follows no redirects and has no directory-listing operation. A final HTTP response is retained across resume, including errors. A 401/403/418/429/451 latches the host stop across restarts. Clearing that latch requires a separate operator decision.

An OS lock permits one writer per run directory and releases after process death. The collector pins candidate, seal, access and generated-plan hashes. Complete plan and pin files are published by atomic rename after flushing their temporary files. An interrupted temporary file remains under `data/`, while the canonical path stays unpublished. Journal segments are append-only. Each request has a durable intent, a durable response-header record and, when available, a completion record with the raw CHECKSUM body encoded as base64 and its hash. CHECKSUM bytes in this journal remain under ignored `data/`. This representation also preserves malformed CHECKSUM evidence within the 2 KB cap. HEAD must succeed before its CHECKSUM can be requested. If a process dies after receiving a response but before the response record becomes durable, one retry is possible. Each unanswered operation has at most two attempts across all resumes. A recorded response with a missing body is reported without a repeated request. A torn final journal line is preserved, pinned and disclosed in the next segment. Corruption in a completed line fails validation.

Resume requires an existing journal and audits its request linkage, scope, ordering, CHECKSUM bytes and plan pins before opening a connection. It recovers the interruption window between publication of the daily-plan file and recording its journal pin. An unclean-termination timestamp denotes detection at resume, with the last durable event bounding the prior stop. Interrupted temporary files and original journal bytes are preserved.

An error writing or syncing a journal aborts its writer. The collector closes the affected stream and handles recovery from the persisted prefix on resume. Storage errors propagate separately from transport failures. This prevents a failed persistence operation from causing another network request or an overlapping record sequence.

Run these commands from the repository root. `plan` performs no network requests. The monthly phase saves the daily plan and prints its request estimate. After reporting that estimate, resume with `--phase all` to execute the already-approved daily phase. A recorded host stop refuses resume before opening a connection.

```text
uv run --locked python evidence/m2/stage_b.py plan --work-dir data/o2-stage-b-20261120
uv run --locked python evidence/m2/collect.py stage-b --work-dir data/o2-stage-b-20261120 --phase monthly
uv run --locked python evidence/m2/collect.py stage-b --work-dir data/o2-stage-b-20261120 --resume --phase all
uv run --locked python evidence/m2/stage_b.py validate --work-dir data/o2-stage-b-20261120 --output data/o2-stage-b-20261120/summary.json
```

Daily fallback is limited to missing monthly keys in the tentative plan. Possibly active months with NOT_FOUND monthly keys retain that result. A dated notice's effective listing or settlement/delisting day can also select a boundary probe within the tentative plan. Indexed notices retain INDEX_HINT_ONLY status, and their probe dates supply no proven active-period bounds. Publication dates and disputed onboard observations do not supply boundary probes. The accepted DGBUSDT daily boundary excludes dates after 2024-04-01. Proven outside-active months are excluded before requests.

FOUND establishes the presence of the physical archive for a symbol, month or day and series. CHECKSUM availability and validity are reported independently. NOT_FOUND establishes that the requested key was unavailable at observation. It supplies no inactivity decision. Listing and delisting instants, intra-month candle and funding continuity and epoch allocation require separate evidence. Exchange-wide completeness remains INCONCLUSIVE because Stage B cannot discover symbols absent from the approved list.

The offline validator regenerates every expected key and observation from the pinned inputs and journal, checks retry linkage and final-response reuse, validates stop-latch enforcement, CHECKSUM bytes and request ordering, and writes per-key `results.jsonl` under the run directory. The committed summary pins artifact hashes, sizes and row counts and reports per-epoch series coverage, CHECKSUM issues, daily results, duration, interruptions and remaining operations. To compare a committed summary with local evidence, use the following command.

```text
uv run --locked python evidence/m2/stage_b.py validate --work-dir data/o2-stage-b-20261120 --verify-summary evidence/m2/o2-stage-b-summary.json
```

The original three-probe `archives` and `notices` commands and `finalize.py` retain their original scope. Stage B uses its dedicated offline validator. Research manifest approval, payload access, scoring windows, O2 step 2 and O3 numeric choices retain their separate gates.

### Stage B implementation checks

Before the live monthly run, the mocked connection suite verified scope refusals, persistent host stops, CHECKSUM limits, final-response reuse and bounded retries. Follow-up integration cases verify the intentional monthly-to-daily pause, nonzero BLOCKED exit, approved candidate-list hash, atomic publication, plan-pin crash window and pre-network resume audit. HTTP failures retrieving CHECKSUM are distinguished from malformed CHECKSUM bytes while preserving FOUND for a successful ZIP HEAD. The final suite contains 351 tests, including 38 Stage B cases. Native Windows and Docker Linux passed. One intermediate Docker run failed because a test read a candidate artifact absent from the image. Replacing that test input with a synthetic candidate list preserved its rejection assertion, and the complete Docker suite then passed. Two fault-injection regressions initially failed when partial journal writes and sync failures were treated as transport failures. After separating fatal journal errors, both regressions passed and recovery preserved record sequences. Mypy checks all 32 files under `src` and `evidence/m2` in both environments. Stage B fixtures reject live connections.

```text
uv sync --locked
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy src evidence/m2
.venv/Scripts/python.exe -m pytest -q --basetemp=data/o2-stage-b-pytest-storage-final
docker compose -p acgs-o2-stage-b --profile check build acgs check
docker compose -p acgs-o2-stage-b --profile check build check
docker compose -p acgs-o2-stage-b --profile check run --rm check
docker compose -p acgs-o2-stage-b run --rm acgs smoke
docker compose -p acgs-o2-stage-b run --rm acgs smoke
git diff --check
```

The two smoke runs recorded and replayed `35be0f1204fa4a70ff517a6643703bfbc657ee64b8cd0827682a183d3db7bc34`. The existing `access` object and M1 semantic pin match the Stage B base commit. The pinned Gitleaks v8.24.3 image scanned 111 commits without leaks before the phase-resume follow-up. Final artifact pins and final-commit checks follow collection.

The monthly live process was launched from the working tree before commit `99e6a05`. Further hardening covers startup approval, resume auditing, plan publication and storage-failure handling. Resumed sessions load the branch code at invocation. The final offline validator reconstructs observations from the saved request and response records.

The Compose entry point was also exercised offline with the checkout mounted read-only and a separate writable experiment directory under `data/`. It produced a plan byte-identical to the native Windows plan, SHA-256 `854f687ca6c1af3ecf8109c8119f1f5a9e0c86515085e6634d24c038d67288e2`. The following is the exact Windows-host command used. On Linux or WSL, substitute the checkout's absolute Linux path for `C:/dev/acgs` in both mounts. Use a separate run directory for concurrent experiments.

```text
docker compose -p acgs-o2-stage-b --profile check run --rm --volume "C:/dev/acgs:/workspace:ro" --volume "C:/dev/acgs/data:/workspace/data" --workdir /workspace check /app/.venv/bin/python evidence/m2/stage_b.py plan --work-dir data/o2-stage-b-docker-plan
```

## Handoff

- **T-15 within the inventory scope** met the date and access segregation rules. Manifest, payload and holdout approvals are later steps.
- **T-14** has an inventory of provenance gaps. Applying the approved [O3 evidence policy](protocol.md#o3-evidence-policy) and measuring research sensitivity require approved inputs.
- **T-21** has an inventory of mark and funding keys. Assessing settlement-time alignment and intrabar mark coverage requires approved payloads.
- O2 step 2 remains open for ranking, publication lag, contiguous history, exclusions and candidate conflict rules. Final O1 windows, manifest pins and numeric settings under O3–O9 remain open.
- Review the three-symbol matrix before expanding discovery. Retain the limits on notice timing, tier applicability and deduction when applying O3.
