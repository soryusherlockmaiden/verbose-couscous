import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from collect import (
    ARCHIVE,
    CUTOFF,
    DISCOVERY,
    MAX_NOTICE_BYTES,
    NOTICES,
    START,
    archive_url,
    checksum_path,
    discovery,
    request_path,
)

from acgs.history_seal import Window, registered_seal, utc_ms

BASE = "a2c13524a5b75b534650ea9fa4c720b5da244fef"
M1_PATH = Path("evidence/m1/binance-2024.json")
NOTICE = "https://www.binance.com/en/support/announcement/detail/ce74360a3fd444b2b35da7186f8dd0ad"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "DGBUSDT")

if not __debug__:
    raise SystemExit("Run the inventory validator without Python optimization")

parser = argparse.ArgumentParser(description="Validate local O2 observations and render the metadata matrix offline")
parser.add_argument("--work-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, help="Result path (default: o2-inventory.json under --work-dir)")
args = parser.parse_args()
ROOT = args.work_dir.resolve()
if not ROOT.is_relative_to(Path("data").resolve()) or ROOT == Path("data").resolve():
    parser.error("--work-dir must be below data/")
OUT = args.output or ROOT / "o2-inventory.json"


def read(name: str) -> Any:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def request_log(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines()]


monthly: list[dict[str, Any]] = read("monthly.json")
daily: list[dict[str, Any]] = read("daily.json")
archive_log, notice_log = map(request_log, ("archive-requests.jsonl", "notice-requests.jsonl"))
seal = registered_seal()
safe = Window(utc_ms(START), utc_ms(CUTOFF))

m1 = json.loads(M1_PATH.read_text(encoding="utf-8"))
old = json.loads(subprocess.check_output(["git", "show", f"{BASE}:config/m2.json"]))
new = json.loads(Path("config/m2.json").read_text(encoding="utf-8"))
assert old["access"] == new["access"] and new["open"]["O2_universe_and_publication"] is None
assert {row["symbol"] for row in monthly} == set(SYMBOLS)
assert len(monthly) == len({row["url"] for row in monthly}) == 504
assert len(daily) == len({row["url"] for row in daily})
assert set(row["series"] for row in monthly) == {"klines", "markPriceKlines", "fundingRate"}
assert all(record["method"] == "HEAD" and record["url"].endswith(".zip") or
           record["method"] == "GET" and record["url"].endswith(".zip.CHECKSUM")
           for record in archive_log)
by_url = {row["url"]: row for row in monthly + daily}
monthly_by_key = {(f["symbol"], f["month"], f["series"], f["interval"]): f for f in monthly}
requests_by_key = {(request["method"], request["url"]): request for request in archive_log}
for request in archive_log:
    request_path(ARCHIVE, request["method"], request["url"], 2048 if request["method"] == "GET" else 0)
    assert request["url"].removesuffix(".CHECKSUM") in by_url
    if request["method"] == "GET":
        head_request = requests_by_key[("HEAD", request["url"].removesuffix(".CHECKSUM"))]
        assert head_request["http_status"] == 200 and head_request["observed_at"] <= request["requested_at"]
for row in monthly + daily:
    series_key = (row["symbol"], row["month"], row["series"], row["interval"])
    date = row.get("date") or row["month"]
    if "date" in row:
        assert monthly_by_key[series_key]["status"] == "NOT_FOUND" or (
            row["symbol"], date
        ) == ("DGBUSDT", "2024-04-01")
    assert row["url"] == archive_url(row["symbol"], date, row["series"], row["interval"])
    assert row["month"] == date[:7]
    start = utc_ms(date + ("-01" if len(date) == 7 else "") + "T00:00:00Z")
    bundle, _ = seal.check_asset({"source": row["url"], "id": row["symbol"], "kind": row["series"],
                                  "start": start, "end": start + 1})
    assert safe.contains(bundle)
    assert row["status"] in {"FOUND", "NOT_FOUND", "UNVERIFIED", "OUTSIDE_ACTIVE_PERIOD"}
    head = requests_by_key.get(("HEAD", row["url"]))
    checksum = requests_by_key.get(("GET", row["url"] + ".CHECKSUM"))
    if head:
        assert head.get("http_status") == row["head_http_status"]
        assert head["observed_at"] == row["observed_at"]
    else:
        assert row["observed_at"] is None
    if row["status"] == "FOUND":
        assert head and head.get("http_status") == 200 and head["content_length"] == row["size_bytes"]
    if row["sha256"] is not None:
        assert row["status"] == "FOUND" and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        assert checksum and checksum.get("http_status") == 200
        name = row["url"].rsplit("/", 1)[-1]
        snapshot = checksum_path(ROOT, row["url"])
        if (ROOT / "checksums").is_dir():
            body = snapshot.read_bytes().strip().split()
            assert body == [row["sha256"].encode(), name.encode()] or (
                len(body) == 2 and body[0].lower() == row["sha256"].encode() and body[1].lstrip(b"*") == name.encode()
            )
    if row["status"] == "OUTSIDE_ACTIVE_PERIOD":
        assert row["symbol"] == "DGBUSDT" and date >= "2024-05" and row["observed_at"] is None
    if row["status"] == "NOT_FOUND":
        assert head and row["head_http_status"] in (404, 410)
assert sum(row["status"] == "OUTSIDE_ACTIVE_PERIOD" for row in monthly) == 56
assert sum(row["symbol"] == "DGBUSDT" and row["date"] == "2024-04-01" for row in daily) == 7

prior_status = [row for row in m1["listings"] if row["evidence"]["source"] == NOTICE]
prior_maintenance = [row for row in m1["maintenance"] if row["evidence"]["source"] == NOTICE]
assert {row["status"] for row in prior_status} == {"CLOSE_ONLY", "DELISTED"}
assert len(prior_maintenance) == 1
discovery_log = discovery()
purpose_by_url = {item["url"]: item["topic"] for item in discovery_log["selected_candidates"]}
notice_rows = read("notice-http.json")
assert [row["url"] for row in notice_rows] == list(purpose_by_url)
notices_by_url = {record["url"]: record for record in notice_log}
assert len(notices_by_url) == len(notice_log)
for record in notice_log:
    request_path(NOTICES, record["method"], record["url"], MAX_NOTICE_BYTES, purpose_by_url)
notices = []
for row in notice_rows:
    record = notices_by_url.get(row["url"], {})
    assert row["http_status"] == record.get("http_status") and row["retrieved_at"] == record.get("observed_at")
    item = {**row, "topic": purpose_by_url[row["url"]], "publication_at": None, "effective_times": [],
            "sha256": row.get("sha256"), "raw_path": row.get("raw_path"), "snapshot_status": "UNVERIFIED"}
    if row.get("sha256"):
        raw = Path(row["raw_path"]).resolve()
        assert raw.parent == ROOT / "notices"
        item["raw_path"] = raw.relative_to(Path.cwd()).as_posix()
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == row["sha256"]
        item["snapshot_status"] = "RETRIEVED_UNREVIEWED"
    elif row["http_status"] == 202 and record["content_length"] == 0:
        item["snapshot_status"] = "BLOCKED_EMPTY_HTTP_202"
    elif row.get("reason") == "HOST_BLOCKED":
        item["snapshot_status"] = "BLOCKED"
    if row["url"] == NOTICE:
        item.update(
            publication_at="2024-03-25T04:40:00Z",
            effective_times=["2024-03-25T10:30:00Z", "2024-04-01T08:30:00Z", "2024-04-01T09:00:00Z"],
            facts_source=M1_PATH.as_posix(),
            facts=[
                "Accepted M1 preserves the lowest post-update DGBUSDT maintenance tier effective at 10:30 UTC.",
                "DGBUSDT became close-only at 08:30 UTC and was delisted at 09:00 UTC on 2024-04-01.",
            ],
        )
    notices.append(item)

assert len(archive_log) == len({(r["method"], r["url"]) for r in archive_log})
notice_blocked = read("notice-block.json") is not None or any(
    item["snapshot_status"].startswith("BLOCKED") for item in notices
)
archive_stop = read("archive-block.json")
logs = defaultdict(list)
for record in archive_log + notice_log:
    logs[urlsplit(record["url"]).netloc].append(record)
requests = {host: {"total": len(records), "methods": dict(Counter(r["method"] for r in records)),
                   "responses": dict(Counter(str(r.get("http_status") or r["reason"]) for r in records)),
                   "first_requested_at": records[0]["requested_at"], "last_observed_at": records[-1]["observed_at"]}
            for host, records in logs.items()}
m1_checks: Counter[str] = Counter()
for asset in m1["assets"]:
    row = by_url[asset["source"]]
    assert row["m1_sha256"] == asset["sha256"]
    status = "UNVERIFIED" if row["sha256"] is None else ("MATCH" if row["sha256"] == asset["sha256"] else "MISMATCH")
    assert row["m1_checksum_status"] == status
    m1_checks[status] += 1
report = {
    "version": "m2-o2-metadata-inventory-v1",
    "base_commit": BASE,
    "symbols": SYMBOLS,
    "start_inclusive": START,
    "cutoff_exclusive": CUTOFF,
    "status": "BLOCKED" if archive_stop or notice_blocked else "INCONCLUSIVE",
    "market_payload_bytes_downloaded": 0,
    "host_status": {ARCHIVE: "BLOCKED" if archive_stop else "REACHABLE",
                    NOTICES: "BLOCKED" if notice_blocked else "REACHABLE"},
    "archive_stop": archive_stop,
    "requests": requests,
    "indexed_discovery_query_count": sum(len(batch) for batch in discovery_log["query_batches"]),
    "indexed_discovery_record": DISCOVERY.relative_to(Path.cwd()).as_posix(),
    "m1_cross_check": {
        "manifest": M1_PATH.as_posix(), "manifest_file_sha256": hashlib.sha256(M1_PATH.read_bytes()).hexdigest(),
        "pinned_assets": len(m1["assets"]), "corresponding_rows": sum(m1_checks.values()),
        "comparison": dict(m1_checks),
    },
    "notice_attempts": notices,
}
rendered = json.dumps(report, indent=2, ensure_ascii=False)
out = [rendered[:-2] + ","]
for key, entries, suffix in (("monthly", monthly, ","), ("daily_probes", daily, "")):
    out.append(f'  "{key}": [')
    out.extend("    " + json.dumps(row, ensure_ascii=False) + ("," if idx + 1 < len(entries) else "")
               for idx, row in enumerate(entries))
    out.append("  ]" + suffix)
out.append("}")
OUT.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
assert json.loads(OUT.read_text(encoding="utf-8"))["monthly"] == monthly
for name, rows in (("monthly", monthly), ("daily", daily)):
    print(name, len(rows), dict(Counter((r["symbol"], r["status"]) for r in rows)))
    print("checksum issues", dict(Counter(r["checksum_issue"] for r in rows if "checksum_issue" in r)))
    print("expected gaps", [(r["symbol"], r.get("date", r["month"]), r["series"], r["interval"], r.get("reason"))
                           for r in rows if r["status"] in ("UNVERIFIED", "NOT_FOUND")][:30])
print("requests", requests)
print("M1", report["m1_cross_check"])
