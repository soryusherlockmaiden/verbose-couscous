"""Pinned, resumable O2 Stage B metadata collection and offline validation."""

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import time
from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from collect import (
    ACCESS_STOP,
    ARCHIVE,
    CUTOFF,
    SERIES,
    START,
    archive_url,
    request_path,
)

CANDIDATES = Path("evidence/m2/o2-candidates.json")
SEAL = Path("src/acgs/history_seal.json")
CONFIG = Path("config/m2.json")
BOUNDARIES = {"LISTING", "SCHEDULED_SETTLEMENT_AND_DELISTING", "DELISTED"}
PACE = 0.08


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def save_new(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    # Publish only complete files. Interrupted temporary files remain auditable.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".",
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def writer_lock(root: Path) -> Iterator[None]:
    """An OS advisory lock is released automatically even after process death."""
    with (root / ".writer.lock").open("a+b") as stream:
        stream.write(b"\0")
        stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def input_pins() -> dict[str, str]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {
        "candidates_sha256": digest(CANDIDATES.read_bytes()),
        "seal_sha256": digest(SEAL.read_bytes()),
        "access_sha256": digest(encoded(config["access"])),
    }


def check_approval() -> None:
    approved = json.loads(CONFIG.read_text(encoding="utf-8"))["proposed"]["o2_stage_b_inventory"]
    raw = CANDIDATES.read_bytes()
    candidates = json.loads(raw)
    rows = candidates["candidates"]
    if (
        digest(raw) != approved["candidate_list_sha256"] or
        len(rows) != approved["epochs"] or
        len({row["symbol"] for row in rows}) != approved["symbols"] or
        approved["start_inclusive"] != START or approved["cutoff_exclusive"] != CUTOFF
    ):
        raise ValueError("Candidate inventory differs from operator approval")


def month_labels() -> list[str]:
    return [f"{year}-{month:02}" for year in (2023, 2024) for month in range(1, 13)]


def make_plan(candidates: dict[str, Any]) -> dict[str, Any]:
    if candidates["window_start"] != START or candidates["cutoff_exclusive"] != CUTOFF:
        raise ValueError("Candidate windows differ from the approved inventory")
    rows: dict[str, dict[str, Any]] = {}
    boundary: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ids: set[str] = set()
    for candidate in candidates["candidates"]:
        epoch, symbol = candidate["candidate_id"], candidate["symbol"]
        if epoch in ids or not re.fullmatch(r"[A-Z0-9]+", symbol):
            raise ValueError("Duplicate epoch or invalid symbol")
        ids.add(epoch)
        if candidate["contract_type"] != "USD_M_PERPETUAL" or candidate["quote_asset"] != "USDT":
            raise ValueError("Unapproved contract classification")
        planned = set(candidate["planned_active_months"])
        possible = set(candidate["possible_additional_months"])
        outside = set(candidate["outside_active_months"])
        if planned & possible or planned & outside or possible & outside:
            raise ValueError("Overlapping month classifications")
        if planned | possible | outside != set(month_labels()):
            raise ValueError("Every inventory month requires a classification")
        for month in month_labels():
            for kind, interval in SERIES:
                url = archive_url(symbol, month, kind, interval)
                row = rows.setdefault(url, {
                    "url": url, "symbol": symbol, "month": month, "series": kind,
                    "interval": interval, "epochs": [], "planned_epochs": [], "outside_epochs": [],
                })
                row["epochs"].append(epoch)
                if month in planned:
                    row["planned_epochs"].append(epoch)
                if month in outside:
                    row["outside_epochs"].append(epoch)
        for event in candidate["events"]:
            effective = event.get("effective_at")
            if event["kind"] in BOUNDARIES and effective:
                instant = datetime.fromisoformat(effective)
                if instant.utcoffset() != timedelta(0):
                    raise ValueError("Boundary must be UTC")
                day = instant.date().isoformat()
                if START[:10] <= day < CUTOFF[:10] and day[:7] in planned:
                    boundary[symbol + "/" + day].append({"epoch": epoch, "event": event})
    for row in rows.values():
        row["outside"] = row["epochs"] == row["outside_epochs"]
        if row["outside"]:
            # Outside months must retain explicit supporting evidence in the pinned list.
            continue
        request_path(ARCHIVE, "HEAD", row["url"], 0, inventory_keys=rows)
    return {
        "version": "m2-o2-stage-b-plan-v1", "start": START, "cutoff": CUTOFF,
        "monthly": sorted(rows.values(), key=lambda row: row["url"]),
        "boundaries": dict(sorted(boundary.items())),
    }


def initialize(root: Path) -> dict[str, Any]:
    plan = make_plan(json.loads(CANDIDATES.read_text(encoding="utf-8")))
    raw = encoded(plan)
    pins = {**input_pins(), "plan_sha256": digest(raw)}
    if (root / "pins.json").exists():
        if json.loads((root / "pins.json").read_text(encoding="utf-8")) != pins:
            raise ValueError("Pinned inputs changed. Resume refused")
        if (root / "plan.json").read_bytes() != raw:
            raise ValueError("Stored plan differs from regenerated plan")
    else:
        if (root / "plan.json").exists():
            if (root / "plan.json").read_bytes() != raw:
                raise ValueError("Orphaned plan differs from pinned inputs")
        else:
            save_new(root / "plan.json", raw)
        save_new(root / "pins.json", encoded(pins))
    return plan


def read_journal(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    torn = []
    for path in sorted(root.glob("journal-*.jsonl")):
        raw = path.read_bytes()
        lines = raw.splitlines(keepends=True)
        for number, line in enumerate(lines):
            if not line.endswith(b"\n") and number == len(lines) - 1:
                torn.append({"path": path.name, "sha256": digest(raw), "bytes": len(raw),
                             "incomplete_tail_bytes": len(line)})
                break
            record = json.loads(line)
            if record["seq"] != len(records):
                raise ValueError("Invalid journal sequence")
            records.append(record)
    return records, torn


class JournalWriteFailed(RuntimeError):
    """The writer must stop after an uncertain journal write or sync."""


class Journal:
    def __init__(self, root: Path, read_only: bool = False) -> None:
        self.records, self.torn = read_journal(root)
        self.path = root / f"journal-{len(list(root.glob('journal-*.jsonl'))):06}.jsonl"
        self.stream: BinaryIO | None = None if read_only else self.path.open("xb")

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {"seq": len(self.records), "at": stamp(), "event": event, **fields}
        if self.stream is None:
            raise ValueError("Read-only journal")
        raw = encoded(record)
        stream = self.stream
        try:
            if stream.write(raw) != len(raw):
                raise OSError("Incomplete journal write")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException as error:
            self.stream = None
            try:
                stream.close()
            except OSError:
                pass
            if isinstance(error, OSError):
                raise JournalWriteFailed(f"Journal persistence failed for {self.path.name}") from error
            raise
        self.records.append(record)
        return record

    def close(self) -> None:
        if self.stream is not None:
            stream, self.stream = self.stream, None
            stream.close()


class HostStopped(RuntimeError):
    pass


class Client:
    def __init__(
        self, journal: Journal, allowed: set[str],
        connect: Callable[[], Any] = lambda: http.client.HTTPSConnection(ARCHIVE, timeout=12),
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self.journal, self.allowed = journal, allowed
        self.connect, self.pause = connect, pause
        self.conn: Any = None
        self.attempts: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.responses: dict[tuple[str, str], dict[str, Any]] = {}
        self.complete: dict[int, dict[str, Any]] = {}
        for record in journal.records:
            if record["event"] == "intent":
                self.attempts[(record["method"], record["url"])].append(record["seq"])
            elif record["event"] == "response":
                self.responses[(record["method"], record["url"])] = record
            elif record["event"] == "complete":
                self.complete[record["attempt"]] = record
        self.stopped = any(row["http_status"] in ACCESS_STOP for row in self.responses.values())

    def check(self, method: str, url: str) -> dict[str, Any] | None:
        path = request_path(ARCHIVE, method, url, 2048 if method == "GET" else 0,
                            inventory_keys=self.allowed)
        if self.stopped:
            raise HostStopped("Persistent host stop latch")
        key = method, url
        if key in self.responses:
            return self.responses[key]
        prior = self.attempts[key]
        if len(prior) >= 2:
            return None
        if method == "GET":
            head = self.responses.get(("HEAD", url.removesuffix(".CHECKSUM")))
            if head is None or head["http_status"] != 200:
                raise ValueError("CHECKSUM requires a prior successful HEAD")
        intent = self.journal.append("intent", method=method, url=url,
                                     retry_of=prior[-1] if prior else None)
        attempt = intent["seq"]
        prior.append(attempt)
        try:
            if self.conn is None:
                self.conn = self.connect()
            self.conn.request(method, path, headers={"Accept-Encoding": "identity"})
            response = self.conn.getresponse()
            length = response.getheader("Content-Length")
            record = self.journal.append(
                "response", attempt=attempt, method=method, url=url,
                http_status=response.status, content_length=length,
            )
            self.responses[key] = record
            if response.status in ACCESS_STOP:
                self.stopped = True
                self.journal.append("host_stop", attempt=attempt, http_status=response.status)
                raise HostStopped(f"HTTP {response.status} from {ARCHIVE}")
            body, issue = b"", None
            if method == "GET" and response.status == 200:
                if length is not None and (not length.isdecimal() or int(length) > 2048):
                    issue = "CHECKSUM_RESPONSE_TOO_LARGE_OR_INVALID_LENGTH"
                    self.close()
                else:
                    body = response.read(2048)
                    if not response.isclosed():
                        issue = "CHECKSUM_RESPONSE_EXCEEDS_2KB_OR_INCOMPLETE"
                        self.close()
            elif method == "HEAD":
                response.read()
            else:
                self.close()
            done = self.journal.append("complete", attempt=attempt,
                                       body_base64=base64.b64encode(body).decode("ascii"),
                                       body_sha256=digest(body), issue=issue)
            self.complete[attempt] = done
            return record
        except (OSError, http.client.HTTPException) as error:
            self.journal.append("transport_error", attempt=attempt, error=type(error).__name__)
            self.close()
            return self.responses.get(key)
        finally:
            self.pause(PACE)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def result(row: dict[str, Any], client: Client) -> dict[str, Any]:
    outcome = {**row, "status": "UNVERIFIED", "sha256": None, "size_bytes": None}
    if row.get("outside"):
        outcome["status"] = "OUTSIDE_ACTIVE_PERIOD"
        return outcome
    head = client.responses.get(("HEAD", row["url"]))
    if head is None:
        return outcome
    outcome["head_http_status"] = head["http_status"]
    outcome["observed_at"] = head["at"]
    if head["http_status"] in (404, 410):
        outcome["status"] = "NOT_FOUND"
    elif head["http_status"] == 200:
        outcome["status"] = "FOUND"
        length = head["content_length"]
        outcome["size_bytes"] = int(length) if length and length.isdecimal() else None
        checksum = client.responses.get(("GET", row["url"] + ".CHECKSUM"))
        done = client.complete.get(checksum["attempt"]) if checksum else None
        issue = "CHECKSUM_UNAVAILABLE"
        if checksum:
            outcome["checksum_http_status"] = checksum["http_status"]
            if checksum["http_status"] != 200:
                issue = f"CHECKSUM_HTTP_{checksum['http_status']}"
            elif done:
                body = base64.b64decode(done["body_base64"], validate=True)
                match = re.fullmatch(rb"([a-fA-F0-9]{64})[ \t]+\*?([!-~]+)[ \t]*", body.strip())
                issue = done["issue"] or "CHECKSUM_CONTENT_INVALID"
                if not done["issue"] and match:
                    if match[2] == row["url"].rsplit("/", 1)[-1].encode("ascii"):
                        outcome["sha256"] = match[1].decode("ascii").lower()
                        issue = ""
            else:
                issue = "RECORDED_RESPONSE_BODY_UNAVAILABLE"
        if issue:
            outcome["checksum_issue"] = issue
    return outcome


def daily_plan(plan: dict[str, Any], monthly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    daily: dict[str, dict[str, Any]] = {}
    for row in monthly:
        if row["outside"]:
            continue
        days: dict[str, str] = {}
        if row["status"] == "NOT_FOUND" and row["planned_epochs"]:
            year, month = map(int, row["month"].split("-"))
            days.update({f"{row['month']}-{day:02}": "PLANNED_MONTH_NOT_FOUND"
                         for day in range(1, monthrange(year, month)[1] + 1)})
        for key in plan["boundaries"]:
            symbol, day = key.split("/")
            if symbol == row["symbol"] and day[:7] == row["month"]:
                days[day] = "DATED_NOTICE_BOUNDARY"
        for day, reason in sorted(days.items()):
            # The accepted DGB boundary excludes later daily keys even within April.
            if row["symbol"] == "DGBUSDT" and day > "2024-04-01":
                continue
            url = archive_url(row["symbol"], day, row["series"], row["interval"])
            item = {**row, "url": url, "date": day, "probe_reason": reason}
            for field in ("status", "sha256", "size_bytes", "observed_at", "head_http_status",
                          "checksum_http_status", "checksum_issue"):
                item.pop(field, None)
            daily[url] = item
    return sorted(daily.values(), key=lambda row: row["url"])


def collect_rows(client: Client, rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if row.get("outside"):
            continue
        head = client.check("HEAD", row["url"])
        if head is None:
            head = client.check("HEAD", row["url"])
        if head and head["http_status"] == 200:
            checksum = client.check("GET", row["url"] + ".CHECKSUM")
            if checksum is None:
                client.check("GET", row["url"] + ".CHECKSUM")
        if index % 100 == 0:
            print(json.dumps({"at": stamp(), "processed": index, "keys": len(rows),
                              "attempts": sum(map(len, client.attempts.values()))}), flush=True)


def run(root: Path, resume: bool, phase: str = "all") -> None:
    with writer_lock(root):
        existing = list(root.glob("journal-*.jsonl"))
        if existing and not resume:
            raise ValueError("Existing run requires --resume")
        if resume and not existing:
            raise ValueError("Resume requires an existing journal")
        check_approval()
        plan = initialize(root)
        if resume:
            validate(root, root / "resume-audit.json")
        journal = Journal(root)
        allowed = {row["url"] for row in plan["monthly"] if not row["outside"]}
        client = Client(journal, allowed)
        try:
            if client.stopped:
                raise HostStopped("Resume refused by persistent host stop latch")
            lifecycle = [r for r in journal.records if r["event"] in
                         ("start", "resume", "finish", "monthly_finished", "stop", "interruption")]
            if lifecycle and lifecycle[-1]["event"] in ("start", "resume"):
                journal.append("unclean_termination", prior_session=lifecycle[-1]["seq"])
            journal.append("resume" if resume else "start", torn_segments=journal.torn)
            collect_rows(client, plan["monthly"])
            monthly = [result(row, client) for row in plan["monthly"]]
            daily = daily_plan(plan, monthly)
            raw = encoded(daily)
            path = root / "daily-plan.json"
            if path.exists():
                if path.read_bytes() != raw:
                    raise ValueError("Daily plan changed during resume")
            else:
                save_new(path, raw)
            if not any(row["event"] == "daily_plan" for row in journal.records):
                journal.append("daily_plan", sha256=digest(raw), head=len(daily),
                               maximum_checksum_get=len(daily))
            print(json.dumps({"daily_head": len(daily), "daily_maximum_checksum_get": len(daily),
                              "sha256": digest(raw)}), flush=True)
            if phase == "monthly":
                journal.append("monthly_finished")
                return
            client.allowed.update(row["url"] for row in daily)
            collect_rows(client, daily)
            journal.append("finish")
        except HostStopped as error:
            journal.append("stop", reason=str(error))
            print(str(error), flush=True)
            raise SystemExit(2) from error
        except BaseException as error:
            if journal.stream is not None:
                journal.append("interruption", reason=type(error).__name__)
            raise
        finally:
            client.close()
            journal.close()


def validate(root: Path, output: Path, expected: Path | None = None) -> dict[str, Any]:
    """Rebuild all observations from the pinned plan and durable journal."""
    plan = initialize(root)
    monthly_allowed = {row["url"] for row in plan["monthly"] if not row["outside"]}
    # Constructing the offline reader opens no connections.
    reader = Journal(root, read_only=True)
    records, torn = reader.records, reader.torn
    client = Client(reader, monthly_allowed)
    monthly = [result(row, client) for row in plan["monthly"]]
    daily = daily_plan(plan, monthly) if (root / "daily-plan.json").exists() else []
    if (root / "daily-plan.json").exists() and (root / "daily-plan.json").read_bytes() != encoded(daily):
        raise ValueError("Daily plan differs from monthly evidence")
    allowed = monthly_allowed | {row["url"] for row in daily}
    intents: dict[int, dict[str, Any]] = {}
    responded: set[int] = set()
    completed: set[int] = set()
    successful_heads: set[str] = set()
    attempts: Counter[tuple[str, str]] = Counter()
    prior_attempts: dict[tuple[str, str], list[int]] = defaultdict(list)
    responses: Counter[str] = Counter()
    method_status: dict[str, Counter[str]] = defaultdict(Counter)
    methods: Counter[str] = Counter()
    stopped = False
    daily_authorized = False
    for record in records:
        kind = record["event"]
        if kind == "daily_plan":
            if not (root / "daily-plan.json").exists() or record["sha256"] != digest(encoded(daily)):
                raise ValueError("Daily plan journal pin differs")
            if record["head"] != len(daily) or record["maximum_checksum_get"] != len(daily):
                raise ValueError("Daily estimate differs from saved plan")
            daily_authorized = True
        if kind == "intent":
            if stopped:
                raise ValueError("Request follows a host stop")
            method, url = record["method"], record["url"]
            if "/daily/" in url and not daily_authorized:
                raise ValueError("Daily request precedes its recorded plan")
            request_path(ARCHIVE, method, url, 2048 if method == "GET" else 0,
                         inventory_keys=allowed)
            if method == "GET" and url.removesuffix(".CHECKSUM") not in successful_heads:
                raise ValueError("CHECKSUM precedes successful HEAD")
            key = method, url
            previous = prior_attempts[key]
            if any(i in responded for i in previous) or len(previous) >= 2:
                raise ValueError("Repeated final response or exhausted retry")
            if record["retry_of"] != (previous[-1] if previous else None):
                raise ValueError("Retry linkage differs")
            intents[record["seq"]] = record
            previous.append(record["seq"])
            attempts[key] += 1
            methods[method] += 1
        elif kind == "response":
            attempt = record["attempt"]
            intent = intents[attempt]
            if attempt in responded or (intent["method"], intent["url"]) != (record["method"], record["url"]):
                raise ValueError("Invalid response linkage")
            responded.add(attempt)
            responses[str(record["http_status"])] += 1
            method_status[record["method"]][str(record["http_status"])] += 1
            if record["method"] == "HEAD" and record["http_status"] == 200:
                successful_heads.add(record["url"])
            stopped |= record["http_status"] in ACCESS_STOP
        elif kind == "complete":
            if record["attempt"] not in responded or record["attempt"] in completed:
                raise ValueError("Invalid completion linkage")
            completed.add(record["attempt"])
            raw = base64.b64decode(record["body_base64"], validate=True)
            if len(raw) > 2048 or digest(raw) != record["body_sha256"]:
                raise ValueError("Invalid saved CHECKSUM bytes")
    all_rows = monthly + [result(row, client) for row in daily]
    remaining_head = sum(not row.get("outside") and ("HEAD", row["url"]) not in client.responses
                         for row in all_rows)
    remaining_get = sum(row["status"] == "FOUND" and
                        ("GET", row["url"] + ".CHECKSUM") not in client.responses for row in all_rows)
    epochs: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        period = "daily" if "date" in row else "monthly"
        series = row["series"] + ("/" + row["interval"] if row["interval"] else "")
        for epoch in row["epochs"]:
            item = epochs.setdefault(epoch, {"symbol": row["symbol"], "series": {}})
            entry = item["series"].setdefault(series, {"monthly": {}, "daily": {},
                                                      "found_months": [], "checksum_issues": {}})
            status = "OUTSIDE_ACTIVE_PERIOD" if epoch in row["outside_epochs"] else row["status"]
            entry[period][status] = entry[period].get(status, 0) + 1
            if period == "monthly" and status == "FOUND":
                entry["found_months"].append(row["month"])
            if "checksum_issue" in row and status == "FOUND":
                issue = row["checksum_issue"]
                entry["checksum_issues"][issue] = entry["checksum_issues"].get(issue, 0) + 1
    for item in epochs.values():
        item["months_with_any_found_key"] = sorted({m for s in item["series"].values() for m in s["found_months"]})
        for entry in item["series"].values():
            entry["found_months"].sort()
            entry["first_found_month"] = next(iter(entry["found_months"]), None)
            entry["last_found_month"] = entry["found_months"][-1] if entry["found_months"] else None
    matrix = b"".join(encoded(row) for row in all_rows)
    (root / "results.jsonl").write_bytes(matrix)
    artifacts = {p.name: {"sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
                 for p in sorted(root.glob("journal-*.jsonl"))}
    artifacts["results.jsonl"] = {"sha256": digest(matrix), "bytes": len(matrix), "rows": len(all_rows)}
    for name in ("plan.json", "pins.json", "daily-plan.json"):
        path = root / name
        if path.exists():
            artifacts[name] = {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
    events = [row for row in records if row["event"] in
              ("start", "resume", "finish", "monthly_finished", "stop", "interruption",
               "unclean_termination", "daily_plan")]
    report = {
        "version": "m2-o2-stage-b-summary-v1", "pins": input_pins(),
        "exchange_wide_completeness": "INCONCLUSIVE", "market_payload_bytes_downloaded": 0,
        "run_status": "BLOCKED" if stopped else (
            "FINISHED" if events and events[-1]["event"] == "finish" and not remaining_head and not remaining_get
            else "INTERRUPTED_OR_INCOMPLETE"),
        "requests": {"host": ARCHIVE, "methods": dict(methods), "http_status": dict(responses),
                     "by_method_status": {method: dict(counts) for method, counts in method_status.items()},
                     "without_response": len(intents) - len(responded),
                     "retries": sum(count - 1 for count in attempts.values())},
        "remaining": {"head": remaining_head, "checksum_get_for_found_keys": remaining_get},
        "daily_phase": "PLANNED" if (root / "daily-plan.json").exists() else "NOT_YET_PLANNED",
        "monthly": dict(Counter(row["status"] for row in monthly)),
        "daily": dict(Counter(row["status"] for row in all_rows[len(monthly):])),
        "daily_results_ref": "results.jsonl (rows with a date field)",
        "events": events, "torn_segments": torn,
        "duration_seconds": ((datetime.fromisoformat(records[-1]["at"]) -
                              datetime.fromisoformat(records[0]["at"])).total_seconds() if records else 0),
        "first_request_at": next((r["at"] for r in records if r["event"] == "intent"), None),
        "last_response_at": next((r["at"] for r in reversed(records) if r["event"] == "response"), None),
        "artifacts": artifacts, "epochs": epochs,
    }
    if expected is not None and json.loads(expected.read_text(encoding="utf-8")) != report:
        raise ValueError("Committed summary differs from regenerated evidence")
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "collect", "validate"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--phase", choices=("monthly", "all"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-summary", type=Path)
    args = parser.parse_args()
    root = args.work_dir.resolve()
    if not root.is_relative_to(Path("data").resolve()) or root == Path("data").resolve():
        parser.error("--work-dir must be below data/")
    root.mkdir(parents=True, exist_ok=True)
    if args.action == "collect":
        run(root, args.resume, args.phase)
    elif args.action == "validate":
        with writer_lock(root):
            check_approval()
            report = validate(root, args.output or root / "summary.json", args.verify_summary)
            print(json.dumps({key: report[key] for key in ("run_status", "requests", "remaining", "monthly", "daily")}))
    else:
        with writer_lock(root):
            check_approval()
            plan = initialize(root)
            active = sum(not row["outside"] for row in plan["monthly"])
            print(json.dumps({"monthly_head": active, "monthly_maximum_checksum_get": active,
                              "pins": input_pins()}))


if __name__ == "__main__":
    main()
