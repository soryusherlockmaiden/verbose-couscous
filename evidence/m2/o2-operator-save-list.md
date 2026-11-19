# O2 expansion Stage A operator save list

Stage A produced 200 provisional epoch candidates across 199 symbols. The candidate list is pending operator review. This short list prioritizes notices that resolve multiple boundaries or material epoch ambiguity. It reuses the existing BTCUSDT, ETHUSDT and DGBUSDT evidence ledger. The search index returned incomplete coverage, particularly for pre-2023 openings and changes between announcements. [Candidates](o2-candidates.json) · [Discovery log](o2-discovery-expansion.json) · [Existing notices](o2-notices.json)

The candidate statuses are 148 `INDEX_HINT_ONLY`, 3 `OPERATOR_COPY_CONFIRMED` and 49 `UNVERIFIED`. The candidate list retains the two index contracts and the stablecoin pair identified by the notices. No in-scope pre-market perpetual was established by the selected sources. The index-only BNX relaunch gets a separate provisional epoch. Other unresolved lifecycle records include COCOS, DAR, FRONT and OMG. Uncertain openings, endings and symbol spellings are identified in the JSON review reasons.

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
| Tentative plan | 1,679 | 11,753 | 11,753 | 23,506 | 121.0 min |
| Tentative plus all possible months | 4,768 | 33,376 | 33,376 | 66,752 | 343.6 min |

The pacing component alone is 1,880 seconds for the tentative monthly scenario and 5,340 seconds for the wider monthly scenario. The timing calculation adds the collector's 0.08-second post-request pause to the mean observed request times from the accepted three-symbol archive log. The log has 455 HEADs averaging 0.2000 seconds and 454 CHECKSUM GETs averaging 0.2577 seconds. The local log SHA-256 is `0cb22f50393b85ef8959e6ffdd0883af1cd6faea81dae92770190140b5e2045b`. The timestamps were recorded at whole-second precision. The 12-second socket timeout, service latency changes, local overhead and a blocked host can alter wall time.

Targeted daily probes are separate. The current plan has 160 unique dated boundary symbol-days, corresponding to at most 1,120 HEADs and 1,120 CHECKSUM GETs, or about 11.5 additional minutes at the same probe averages if all those keys exist. For each missing monthly key, a full-month daily fallback adds 28–31 HEADs and up to as many CHECKSUM GETs. Restrict fallback to eligible dates using the approved lifecycle evidence. The number of missing keys and final daily cost remain unresolved before Stage B.

The known `OUTSIDE_ACTIVE_PERIOD` skip is `DGBUSDT` from May through December 2024, eight full months and 56 monthly keys. April stays in the plan, including the April 1 daily probe. For other symbols, the possible-month groups preserve poorly evidenced periods for review. [Original inventory](o2-inventory.md#archive-coverage) · [Collector and pacing](collect.py)

Stage B requires operator approval of the candidate list before archive requests. O2 step 2 eligibility, purpose-specific manifest approval, market payload access and prospective holdout registration remain separate.
