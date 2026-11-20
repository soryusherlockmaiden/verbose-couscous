"""One operator-approved supplemental metadata probe; no resume or automatic retry."""

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from collect import ACCESS_STOP, ARCHIVE, archive_url
from stage_b import (
    Client,
    HostStopped,
    Journal,
    check_approval,
    digest,
    input_pins,
    result,
    save_new,
)

URL = archive_url("HIGHUSDT", "2023-09", "markPriceKlines", "15m")
ORIGINAL = Path("data/o2-stage-b-20261120")
SUMMARY = Path("evidence/m2/o2-stage-b-summary.json")
ROOT = Path("data/o2-stage-b-highusdt-20261120")


def probe(client: Client, row: dict[str, Any]) -> dict[str, Any]:
    """Issue at most one HEAD and, only after HTTP 200, one CHECKSUM GET."""
    if row["url"] != URL or row.get("outside"):
        raise ValueError("Supplemental approval covers only the HIGHUSDT September key")
    head = client.check("HEAD", URL)
    if head is not None and head["http_status"] == 200:
        client.check("GET", URL + ".CHECKSUM")
    return result(row, client)


def main() -> None:
    check_approval()
    raw_summary = SUMMARY.read_bytes()
    summary = json.loads(raw_summary)
    if summary["pins"] != input_pins():
        raise ValueError("Supplemental probe inputs differ from the original run")
    if any(int(status) in ACCESS_STOP for status in summary["requests"]["http_status"]):
        raise HostStopped("Original host stop refuses a supplemental request")
    original_hashes = {}
    for name, artifact in summary["artifacts"].items():
        raw = (ORIGINAL / name).read_bytes()
        original_hashes[name] = digest(raw)
        if original_hashes[name] != artifact["sha256"] or len(raw) != artifact["bytes"]:
            raise ValueError("Original artifact differs from its committed pin")
    plan = json.loads((ORIGINAL / "plan.json").read_text(encoding="utf-8"))
    row = next(item for item in plan["monthly"] if item["url"] == URL)
    # This fixed, exclusive directory consumes this one supplemental approval.
    ROOT.mkdir(exist_ok=False)
    journal = Journal(ROOT)
    client = Client(journal, {URL})
    started = time.monotonic()
    blocked = False
    try:
        journal.append("supplemental_start", maximum_head=1, maximum_checksum_get=1)
        try:
            observation = probe(client, row)
        except HostStopped:
            blocked = True
            observation = result(row, client)
        journal.append("supplemental_finish", blocked=blocked)
    finally:
        client.close()
        journal.close()
    elapsed = time.monotonic() - started
    if SUMMARY.read_bytes() != raw_summary or any(
        digest((ORIGINAL / name).read_bytes()) != sha for name, sha in original_hashes.items()
    ):
        raise ValueError("Original evidence changed during the supplemental probe")
    records = journal.records
    requests = Counter(r["method"] for r in records if r["event"] == "intent")
    report = {
        "version": "m2-o2-highusdt-supplement-v1",
        "operator_approval": {"date_utc": "2026-11-20", "maximum_head": 1,
                              "maximum_checksum_get": 1, "automatic_retries": 0},
        "original_summary_sha256": digest(raw_summary),
        "original_artifacts_unchanged": True,
        "original_artifact_hashes": original_hashes,
        "original_unverified_status_preserved": True,
        "host": ARCHIVE, "requests": dict(requests),
        "http_status": dict(Counter(str(r["http_status"]) for r in records
                                    if r["event"] == "response")),
        "duration_seconds": elapsed, "blocked": blocked,
        "market_payload_bytes_downloaded": 0, "observation": observation,
        "journal": {"path": journal.path.as_posix(), "rows": len(records),
                    "bytes": journal.path.stat().st_size,
                    "sha256": digest(journal.path.read_bytes())},
    }
    save_new(ROOT / "report.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report), flush=True)
    if blocked:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
