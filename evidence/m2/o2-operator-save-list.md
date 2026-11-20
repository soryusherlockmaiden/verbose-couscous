# O2 expansion Stage A operator save list

Stage A contains 342 provisional epoch candidates across 340 symbols. On 2026-11-20 UTC, the operator approved this list for Stage B metadata inventory, including planned and all possible additional months. The candidate JSON remains the pinned Stage A artifact, with the later approval recorded in the protocol and proposed configuration. Exchange-wide completeness remains INCONCLUSIVE. This short list prioritizes notices that resolve multiple boundaries or material epoch ambiguity. It reuses the existing BTCUSDT, ETHUSDT and DGBUSDT evidence ledger. [Candidates](o2-candidates.json) · [Discovery log](o2-discovery-expansion.json) · [Existing notices](o2-notices.json)

The candidate statuses are 148 `INDEX_HINT_ONLY`, 3 `OPERATOR_COPY_CONFIRMED`, 121 `SNAPSHOT_OBSERVED`, 21 `TRADING_RULES_OBSERVED` and 49 `UNVERIFIED`. The candidate list retains the two index contracts and the stablecoin pair identified by the notices. Other unresolved lifecycle records include COCOS, DAR, FRONT and OMG. Uncertain openings, endings and symbol spellings are identified in the JSON review reasons.

## Historical exchangeInfo evidence

Two Wayback copies of the Binance USD-M exchange JSON supply dated observations. The compressed bodies are stored locally under `data/o2-inventory/snapshots-wayback/`. Their raw SHA-256 hashes and Wayback CDX digests, capture and retrieval times, original HTTP dates and separate `serverTime` values are pinned in the [snapshot evidence ledger](o2-exchangeinfo-snapshots.json).

| Capture (UTC) | USDT perpetual rows | `TRADING` | `SETTLING` |
|---|---:|---:|---:|
| 2022-11-27 17:47:56 | 153 | 147 | 6 |
| 2023-11-02 09:32:09 | 226 | 218 | 8 |

The 379 symbol observations retain `PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `MIN_NOTIONAL` and `PERCENT_PRICE` without changing decimal strings. The Wayback capture time supplies `as_of`. Both JSON `serverTime` values precede capture. Filter validity beyond the observation needs an approved O3 rule. Earlier decisions require independently admissible evidence. [O3 evidence policy](protocol.md#o3-evidence-policy)

The archive contributes 120 new symbols, including 117 observed `TRADING` at least once and three observed only `SETTLING`. The three settling-only symbols are provisional leads. Among the new symbols, 116 are observed `TRADING` inside the window. Each new in-window `TRADING` observation adds its capture month to the tentative plan. Other months remain possible until evidence resolves them. Existing evidence labels are retained for lifecycle claims, with snapshot statuses recorded separately.

- **BNXUSDT.** `onboardDate` is `2022-03-31T07:00:00.000Z` in the 2022 copy and `2023-02-22T07:00:00.000Z` in the 2023 copy. The relaunch notice instead schedules trading at `2023-02-22T14:45:00Z`. Keep both timestamps and the two epoch rows.
- **TLMUSDT.** The first copy shows `SETTLING` with `onboardDate` `2021-07-15T07:00:00.000Z`. The second shows `TRADING` with `onboardDate` `2023-03-29T00:00:00.000Z`. The indexed launch notice schedules `2023-03-30T12:30:00Z`. Keep the older observation in a separate provisional epoch and preserve the later notice time.

Reproduce the ledger offline from the pinned raw bodies with this command. Compare the generated file with the committed ledger.

```text
uv run --locked python evidence/m2/extract_exchangeinfo.py --output data/o2-inventory/o2-exchangeinfo-snapshots-recheck.json
```

Coverage between snapshots and throughout 2024 remains `INCONCLUSIVE`. The directory completeness check is `BLOCKED` pending a source that enforces the cutoff before returning names. The current Binance Vision prefix may mix pre-cutoff and later symbols, and folder names lack listing dates. `INDEX_GAP` and discarded-name counts remain unknown.

## Historical archive listing check

A1 found zero usable S3 XML responses for the monthly USD-M kline prefix. The historically reconstructed exact request returned no CDX match. The May 22, 2024 capture contained the HTML file browser, and its archived script constructs a separate S3 request for the listing. The offline check pinned these bytes without executing JavaScript. Several broader CDX checks timed out, so the archive search cannot establish absence beyond the successful queries. `INDEX_GAP` remains unknown. [A1 query and replay evidence](o2-a1-wayback.json)

The additional 2024 `exchangeInfo` checks found no usable capture. The exact path returned 26 CDX rows with original HTTP `451`, while a query-variant prefix search timed out. Two captured Binance trading-rules pages did contain embedded USD-M product records. [Trading-rules evidence](o2-trading-rules-snapshots.json)

| Wayback capture (UTC) | Embedded product records read | USDT perpetual names | Completeness of the embedded array |
|---|---:|---:|---|
| 2024-03-01 07:23:49 | 273 | 259 | Complete in the captured data |
| 2024-07-15 17:28:35 | 174 | 173 | Partial. The archived bytes end inside the array |

The 173 July names also occur in March with the same reported onboard dates. March adds 21 provisional names to the prior candidate list. Only names and reported `onboardDate` values are retained from the pages. The candidate file records these observations separately from the `exchangeInfo` status and filter evidence. New names enter the possible-month estimate. Active months and coverage throughout 2024 still require independent evidence. The raw HTML bodies remain under ignored `data/o2-inventory/a1-wayback-review/`.

```text
uv run --locked python evidence/m2/extract_trading_rules.py --output data/o2-inventory/o2-trading-rules-snapshots-recheck.json
```

### Independent replay size check

On 2026-11-20, two fresh GETs to the pinned Wayback identity replay URLs returned HTTP 200. Each request used `curl.exe --fail --max-filesize 67108864 --max-time 90 -H "Accept-Encoding: identity"` (64 MiB maximum per response), without a Range header or redirect following. Both transfers exited successfully. The response headers and fresh bodies are under ignored `data/o2-inventory/a1-wayback-review/recheck/`. Each response's `Memento-Datetime` matched the requested capture.

| Replay response time (UTC) | Capture | Content-Length | x-archive-orig-content-length | Received bytes | Computed SHA-1 (base32), matching the recorded CDX row |
|---|---|---:|---:|---:|---|
| 00:52:22 | March | 1,048,576 | 1,048,576 | 1,048,576 | `6UCZ2NGLJHHKYHSB75UXYRQQ3ALOFTQT` |
| 00:52:25 | July | 1,048,576 | 1,048,576 | 1,048,576 | `HMQ4FPTJ4YAYAR7F3XY73FERP2DHC5KB` |

The full CDX-matching July body ends inside the product array. These headers and digests establish the extent of the stored representation. They do not identify which upstream component imposed the size limit. Running the extractor against the fresh bodies reproduced the committed snapshot ledger byte for byte (SHA-256 `30f7233d98c3613b729123a1c8563429dc65c7d9a9ad52a9c9932282eeb078ce`). The recheck adds zero names or epochs, so the Stage B estimates below still apply. [Recorded CDX rows](o2-a1-wayback.json)

## Priority notices

| Notice | Indexed publication day | Suggested PDF filename | What to verify |
|---|---|---|---|
| [BNX suspension and redenomination](https://www.binance.com/en/support/announcement/detail/4d23ada51a2e4fa182835c77d51ba1a9) | 2023-02-10 | `BNXUSDT-2023-02-10-redenomination.pdf` | 2023 BNX close-only, settlement and separate relisting requirement. |
| [BNX futures relaunch](https://www.binance.com/en/support/announcement/detail/940d0e48493e4627889c3f46371df70b) | 2023-02-22 | `BNXUSDT-2023-02-22-relaunch.pdf` | Start of the successor BNX epoch with the same exchange symbol. |
| [MAVIA, OMG and BOND delisting schedule](https://www.binance.com/en/support/announcement/detail/27eac260521b4f40ab331e1a345887a7) | 2024-12-06 | `MAVIA-OMG-BOND-2024-12-06-delisting.pdf` | Original schedule for three symbols. Keep the later OMG correction separate. |
| [Latest in-scope OMG postponement](https://www.binance.com/en/support/announcement/detail/1e43c128806544018d3cc537a8add3f6) | 2024-12-20 | `OMGUSDT-2024-12-20-postponement.pdf` | Resolve the conflict in the December 2024 OMG schedule. |
| [Two May settlement batches](https://www.binance.com/en/support/announcement/detail/cb525a5fbb954935afc77630fc5bd0cc) | 2024-05-08 | `IDEX-SLP-GLMR-MDT-AUDIO-2024-05-08-delisting.pdf` | Separate the dates for IDEX, SLP and GLMR from those for MDT and AUDIO. |
| [XEM, ORBS and LOOM delisting](https://www.binance.com/en/support/announcement/detail/cfe97d52a8c840d3ba631835b637e9e7) | 2024-11-29 | `XEM-ORBS-LOOM-2024-11-29-delisting.pdf` | Confirm three December close-only and settlement boundaries from one notice. |
| [COCOS token swap with futures settlement](https://www.binance.com/en/support/announcement/detail/45852dc155b641bc9e1c23bc41d8ded6) | 2023-05-18 | `COCOSUSDT-2023-05-18-token-swap.pdf` | Confirm the old futures boundary and separate the underlying rebrand from futures listing evidence. |
| [FRONT token swap with futures settlement](https://www.binance.com/en/support/announcement/detail/3ab5488a00e04d4fb338c77ea28326a8) | 2024-08-19 | `FRONTUSDT-2024-08-19-token-swap.pdf` | Confirm the old futures boundary and clarify which futures facts the notice states. |
| [DAR token swap with futures settlement](https://www.binance.com/en/support/announcement/detail/21551cde98784f64b374f8d50ff2a438) | 2024-12-19 | `DARUSDT-2024-12-19-token-swap.pdf` | Confirm the December old-contract boundary while the successor futures epoch remains unresolved. |

## Save and import a copy

1. Open an individual notice from the table in your regular browser. Confirm that the URL corresponds to that exact notice and the notice was published before `2025-01-01T00:00:00Z`. Leave results pending if the date, timezone near the cutoff or notice identity is ambiguous. If the site refuses access, record the failure and stop.
2. Use **Print → Save as PDF**, with headers and footers enabled so the source URL is visible. Save the PDF in `data/o2-inventory/notices-operator/`. Create the directory if needed. Record the save instant in UTC and transcribe the displayed publication date or timestamp as printed. Keep the PDF outside Git.
3. From the repository root, use the exact notice URL and the recorded values. Replace the two quoted placeholders in this example with your actual UTC save instant and the publication display from the page.

```text
uv run --locked python evidence/m2/ingest_notices.py --url "https://www.binance.com/en/support/announcement/detail/940d0e48493e4627889c3f46371df70b" --file "data/o2-inventory/notices-operator/BNXUSDT-2023-02-22-relaunch.pdf" --saved-at "YYYY-MM-DDTHH:MM:SSZ" --published-display "publication date or time shown in the notice"
```

4. The offline command hashes the exact bytes, prints the SHA-256 and writes the provenance and status to `evidence/m2/o2-notices.json`. To register reviewed facts, place an optional JSON array in the same local directory and rerun the command with `--facts PATH`. Each fact needs a candidate `symbol` and a `kind`. Include only values the notice actually states, with separate facts for close-only and scheduled settlement when both are stated. Use `null` for unknown exact times. Keep the same URL, bytes, save instant and publication display when transcribing an already registered copy.
5. Review the ledger diff. A copy without reviewed facts remains `PENDING_REVIEW`. Repeating the identical import is idempotent. The helper refuses conflicting evidence. Recording a copy adds provenance to the ledger. Updating the candidate assessment after reviewing those facts is a separate review step.

## Stage B request and duration estimates

An archive key is unique by symbol, period and series. The seven series are three contract kline intervals, three mark kline intervals and funding. This gives seven monthly `HEAD` requests per unique symbol–month and up to one CHECKSUM `GET` per successful `HEAD`. The upper scenario includes possible months for the discovered candidates only. The February BNX overlap across two provisional epochs contributes one set of monthly keys.

| Monthly scenario | Symbol–months | HEAD | CHECKSUM GET if every HEAD succeeds | Requests if all found | Serial time at probe averages |
|---|---:|---:|---:|---:|---:|
| Tentative plan | 1,835 | 12,845 | 12,845 | 25,690 | 132.2 min |
| Tentative plus all possible months | 8,152 | 57,064 | 57,064 | 114,128 | 587.5 min |

The pacing component alone is 2,055 seconds for the tentative monthly scenario and 9,130 seconds for the wider monthly scenario. The timing calculation adds the collector's 0.08-second post-request pause to the mean observed request times from the accepted three-symbol archive log. The log has 455 HEADs averaging 0.2000 seconds and 454 CHECKSUM GETs averaging 0.2577 seconds. The local log SHA-256 is `0cb22f50393b85ef8959e6ffdd0883af1cd6faea81dae92770190140b5e2045b`. The timestamps were recorded at whole-second precision. The 12-second socket timeout, service latency changes, local overhead and a blocked host can alter wall time.

Targeted daily probes are separate. The current plan has 160 unique dated boundary symbol-days, corresponding to at most 1,120 HEADs and 1,120 CHECKSUM GETs, or about 11.5 additional minutes at the same probe averages if all those keys exist. For each missing monthly key, a full-month daily fallback adds 28–31 HEADs and up to as many CHECKSUM GETs. Restrict fallback to eligible dates using the approved lifecycle evidence. The number of missing keys and final daily cost remain unresolved before Stage B.

The known `OUTSIDE_ACTIVE_PERIOD` skip is `DGBUSDT` from May through December 2024, eight full months and 56 monthly keys. April stays in the plan, including the April 1 daily probe. For other symbols, the possible-month groups preserve poorly evidenced periods for review. [Original inventory](o2-inventory.md#archive-coverage) · [Collector and pacing](collect.py)

The operator approved Stage B on 2026-11-20 UTC. O2 step 2 eligibility, purpose-specific manifest approval, market payload access and prospective holdout registration remain separate. [Stage B method](o2-inventory.md#stage-b-expanded-metadata-inventory)
