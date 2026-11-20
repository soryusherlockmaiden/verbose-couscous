"""Crash, access and evidence regressions for the offline-testable Stage B client."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "evidence/m2"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("stage_b", SCRIPTS / "stage_b.py")
    assert spec is not None and spec.loader is not None
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
finally:
    sys.path.remove(str(SCRIPTS))

URL = stage.archive_url("BTCUSDT", "2024-01", "klines", "15m")
BODY = b"a" * 64 + b"  BTCUSDT-15m-2024-01.zip\n"


class Response:
    def __init__(self, status: int = 200, body: bytes = b"", length: str | None = None) -> None:
        self.status, self.body, self.offset = status, body, 0
        self.length = str(len(body)) if length is None else length

    def getheader(self, name: str) -> str:
        assert name == "Content-Length"
        return self.length

    def read(self, size: int | None = None) -> bytes:
        end = len(self.body) if size is None else self.offset + size
        raw = self.body[self.offset:end]
        self.offset += len(raw)
        return raw

    def isclosed(self) -> bool:
        return self.offset == len(self.body)


class Connection:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = iter(replies)
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        assert headers == {"Accept-Encoding": "identity"}
        self.requests.append((method, path))

    def getresponse(self) -> Response:
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self) -> None:
        pass


def client(root: Path, replies: list[Any]) -> tuple[Any, Any, Connection]:
    journal = stage.Journal(root)
    connection = Connection(replies)
    return stage.Client(journal, {URL}, connect=lambda: connection, pause=lambda _: None), journal, connection


def candidate(symbol: str = "BTCUSDT", epoch: str = "first") -> dict[str, Any]:
    return {"candidate_id": symbol + "@" + epoch, "symbol": symbol,
            "contract_type": "USD_M_PERPETUAL", "quote_asset": "USDT",
            "planned_active_months": ["2024-01"], "outside_active_months": [],
            "possible_additional_months": [m for m in stage.month_labels() if m != "2024-01"],
            "events": []}


def plan(rows: list[dict[str, Any]]) -> Any:
    return stage.make_plan({"window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
                            "candidates": rows})


def approve_fixture(root: Path, monkeypatch: Any) -> None:
    source = root / "candidates.json"
    raw = stage.encoded({"window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
                         "candidates": [candidate()]})
    source.write_bytes(raw)
    config = root / "config.json"
    config.write_bytes(stage.encoded({"access": {}, "proposed": {"o2_stage_b_inventory": {
        "candidate_list_sha256": stage.digest(raw), "symbols": 1, "epochs": 1,
        "start_inclusive": stage.START, "cutoff_exclusive": stage.CUTOFF,
    }}}))
    monkeypatch.setattr(stage, "CANDIDATES", source)
    monkeypatch.setattr(stage, "CONFIG", config)


@pytest.mark.parametrize("status", [401, 403, 418, 429, 451])
def test_host_stop_survives_restart(tmp_path: Path, status: int) -> None:
    first, journal, conn = client(tmp_path, [Response(status)])
    with pytest.raises(stage.HostStopped):
        first.check("HEAD", URL)
    journal.close()
    second, next_journal, resumed = client(tmp_path, [])
    with pytest.raises(stage.HostStopped):
        second.check("HEAD", URL)
    next_journal.close()
    assert len(conn.requests) == 1 and resumed.requests == []


@pytest.mark.parametrize("method,url", [
    ("GET", URL), ("HEAD", URL + "?x=1"), ("HEAD", URL.replace("2024-01", "2025-01")),
    ("HEAD", URL.replace("BTCUSDT", "ETHUSDT")),
    ("HEAD", "https://data.binance.vision/?prefix=data/"),
    ("HEAD", URL.replace("data.binance.vision", "www.binance.com")),
])
def test_scope_refused_before_request(tmp_path: Path, method: str, url: str) -> None:
    current, journal, conn = client(tmp_path, [])
    with pytest.raises(ValueError):
        current.check(method, url)
    journal.close()
    assert not conn.requests
    assert not current.attempts


def test_synthetic_registered_seal_refused_before_request(tmp_path: Path, monkeypatch: Any) -> None:
    from acgs.history_seal import HistorySeal, Window, utc_ms

    guard = sys.modules[stage.request_path.__module__]
    sealed = Window(utc_ms("2024-01-15T00:00:00Z"), utc_ms("2024-01-16T00:00:00Z"))
    monkeypatch.setattr(guard, "registered_seal", lambda: HistorySeal((sealed,)))
    current, journal, conn = client(tmp_path, [])
    with pytest.raises(ValueError, match="sealed"):
        current.check("HEAD", URL)
    journal.close()
    assert not conn.requests and not current.attempts


def test_resume_skips_responses_and_retains_checksum_bytes(tmp_path: Path) -> None:
    first, journal, _ = client(tmp_path, [Response(), Response(body=BODY)])
    first.check("HEAD", URL)
    first.check("GET", URL + ".CHECKSUM")
    journal.close()
    second, next_journal, conn = client(tmp_path, [])
    second.check("HEAD", URL)
    second.check("GET", URL + ".CHECKSUM")
    row = stage.result({"url": URL}, second)
    next_journal.close()
    assert row["sha256"] == "a" * 64
    assert not conn.requests


def test_response_before_body_crash_is_final(tmp_path: Path, monkeypatch: Any) -> None:
    first, journal, _ = client(tmp_path, [Response(), Response(body=BODY)])
    first.check("HEAD", URL)
    original = journal.append

    def crash(event: str, **fields: Any) -> Any:
        if event == "complete":
            raise KeyboardInterrupt
        return original(event, **fields)

    monkeypatch.setattr(journal, "append", crash)
    with pytest.raises(KeyboardInterrupt):
        first.check("GET", URL + ".CHECKSUM")
    journal.close()
    second, next_journal, conn = client(tmp_path, [])
    second.check("GET", URL + ".CHECKSUM")
    assert stage.result({"url": URL}, second)["checksum_issue"] == "RECORDED_RESPONSE_BODY_UNAVAILABLE"
    next_journal.close()
    assert not conn.requests


def test_unanswered_intent_has_one_retry_across_all_resumes(tmp_path: Path) -> None:
    first, journal, _ = client(tmp_path, [KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        first.check("HEAD", URL)
    journal.close()
    second, next_journal, conn = client(tmp_path, [OSError("synthetic network failure")])
    assert second.check("HEAD", URL) is None
    next_journal.close()
    third, final_journal, last_conn = client(tmp_path, [])
    assert third.check("HEAD", URL) is None
    final_journal.close()
    intents = [r for r in third.journal.records if r["event"] == "intent"]
    assert len(intents) == 2 and intents[1]["retry_of"] == intents[0]["seq"]
    assert len(conn.requests) == 1 and not last_conn.requests


def test_crash_after_intent_before_network_is_recoverable(tmp_path: Path, monkeypatch: Any) -> None:
    first, journal, conn = client(tmp_path, [])
    original = journal.append

    def crash(event: str, **fields: Any) -> Any:
        original(event, **fields)
        raise KeyboardInterrupt

    monkeypatch.setattr(journal, "append", crash)
    with pytest.raises(KeyboardInterrupt):
        first.check("HEAD", URL)
    journal.close()
    assert not conn.requests
    second, resumed_journal, resumed = client(tmp_path, [Response(404)])
    second.check("HEAD", URL)
    resumed_journal.close()
    assert len(resumed.requests) == 1
    assert second.journal.records[1]["retry_of"] == 0


def test_redirect_is_final_without_following_location(tmp_path: Path) -> None:
    first, journal, conn = client(tmp_path, [Response(302)])
    first.check("HEAD", URL)
    journal.close()
    second, resumed_journal, resumed = client(tmp_path, [])
    second.check("HEAD", URL)
    resumed_journal.close()
    assert len(conn.requests) == 1 and not resumed.requests


def test_checksum_requires_head_and_caps_body(tmp_path: Path) -> None:
    current, journal, conn = client(tmp_path, [Response(), Response(body=b"x" * 3000)])
    with pytest.raises(ValueError):
        current.check("GET", URL + ".CHECKSUM")
    assert not conn.requests
    current.check("HEAD", URL)
    current.check("GET", URL + ".CHECKSUM")
    outcome = stage.result({"url": URL}, current)
    journal.close()
    assert outcome["status"] == "FOUND" and outcome["sha256"] is None
    assert outcome["checksum_issue"] == "CHECKSUM_RESPONSE_TOO_LARGE_OR_INVALID_LENGTH"


@pytest.mark.parametrize("status", [302, 404, 500])
def test_checksum_http_error_preserves_found_archive(tmp_path: Path, status: int) -> None:
    current, journal, _ = client(tmp_path, [Response(), Response(status)])
    current.check("HEAD", URL)
    current.check("GET", URL + ".CHECKSUM")
    outcome = stage.result({"url": URL}, current)
    journal.close()
    assert outcome["status"] == "FOUND" and outcome["sha256"] is None
    assert outcome["checksum_issue"] == f"CHECKSUM_HTTP_{status}"


def test_shared_epoch_bundle_is_requested_once() -> None:
    current = plan([candidate(epoch="old"), candidate(epoch="new")])
    assert len(current["monthly"]) == 168
    assert all(len(row["epochs"]) == 2 for row in current["monthly"])


def test_daily_probes_require_planned_gap_or_notice_boundary() -> None:
    row = candidate()
    row["events"] = [{"kind": "LISTING", "effective_at": "2024-01-15T12:00:00Z",
                      "evidence_status": "INDEX_HINT_ONLY"}]
    current = plan([row])
    monthly = [{**r, "status": "FOUND"} for r in current["monthly"]]
    assert len(stage.daily_plan(current, monthly)) == 7
    for item in monthly:
        item["status"] = "NOT_FOUND"
    daily = stage.daily_plan(current, monthly)
    assert len(daily) == 31 * 7
    assert {r["month"] for r in daily} == {"2024-01"}


def test_outside_dgb_months_refused_even_if_in_allowed_set() -> None:
    url = stage.archive_url("DGBUSDT", "2024-05", "fundingRate", None)
    with pytest.raises(ValueError):
        stage.request_path(stage.ARCHIVE, "HEAD", url, 0, inventory_keys={url})


def test_torn_tail_is_preserved_and_recovery_can_append(tmp_path: Path) -> None:
    journal = stage.Journal(tmp_path)
    journal.append("start")
    journal.close()
    with journal.path.open("ab") as stream:
        stream.write(b'{"seq":1')
    original = journal.path.read_bytes()
    resumed = stage.Journal(tmp_path)
    resumed.append("resume", torn_segments=resumed.torn)
    resumed.close()
    records, torn = stage.read_journal(tmp_path)
    assert [r["event"] for r in records] == ["start", "resume"]
    assert torn[0]["incomplete_tail_bytes"] == 8
    assert journal.path.read_bytes() == original


def test_single_writer_lock(tmp_path: Path) -> None:
    with stage.writer_lock(tmp_path), pytest.raises(OSError), stage.writer_lock(tmp_path):
        pass


def test_plan_pins_reject_changed_inputs(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "candidates.json"
    source.write_bytes(stage.encoded({"window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
                                     "candidates": [candidate()]}))
    monkeypatch.setattr(stage, "CANDIDATES", source)
    stage.initialize(tmp_path)
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Pinned inputs changed"):
        stage.initialize(tmp_path)


def test_offline_validator_rejects_post_stop_request(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "candidates.json"
    source.write_bytes(stage.encoded({"window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
                                     "candidates": [candidate()]}))
    monkeypatch.setattr(stage, "CANDIDATES", source)
    stage.initialize(tmp_path)
    current, journal, _ = client(tmp_path, [Response(403)])
    with pytest.raises(stage.HostStopped):
        current.check("HEAD", URL)
    journal.append("intent", method="HEAD", url=URL, retry_of=None)
    journal.close()
    with pytest.raises(ValueError, match="follows a host stop"):
        stage.validate(tmp_path, tmp_path / "summary.json")


def test_validation_reproduces_summary_and_detects_body_corruption(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "candidates.json"
    source.write_bytes(stage.encoded({"window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
                                     "candidates": [candidate()]}))
    monkeypatch.setattr(stage, "CANDIDATES", source)
    stage.initialize(tmp_path)
    current, journal, _ = client(tmp_path, [Response(), Response(body=BODY)])
    current.check("HEAD", URL)
    current.check("GET", URL + ".CHECKSUM")
    journal.close()
    first = stage.validate(tmp_path, tmp_path / "summary.json")
    assert stage.validate(tmp_path, tmp_path / "summary.json") == first
    assert first["monthly"] == {"FOUND": 1, "UNVERIFIED": 167}
    daily_path = tmp_path / "daily-plan.json"
    daily_path.write_bytes(b"[{}]\n")
    with pytest.raises(ValueError, match="Daily plan differs"):
        stage.validate(tmp_path, tmp_path / "summary.json")
    daily_path.unlink()
    expected = tmp_path / "incorrect-summary.json"
    expected.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Committed summary"):
        stage.validate(tmp_path, tmp_path / "summary.json", expected)
    data = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    data[-1]["body_sha256"] = "0" * 64
    journal.path.write_bytes(b"".join(stage.encoded(r) for r in data))
    with pytest.raises(ValueError, match="CHECKSUM bytes"):
        stage.validate(tmp_path, tmp_path / "summary.json")


def test_run_monthly_then_daily_resume_skips_completed_keys(tmp_path: Path, monkeypatch: Any) -> None:
    approve_fixture(tmp_path, monkeypatch)
    real_client = stage.Client
    conn = Connection([Response(404) for _ in range(500)])
    monkeypatch.setattr(stage, "Client", lambda journal, keys: real_client(
        journal, keys, connect=lambda: conn, pause=lambda _: None,
    ))
    stage.run(tmp_path, False, "monthly")
    assert len(conn.requests) == 168
    stage.run(tmp_path, True)
    assert len(conn.requests) == 168 + 31 * 7
    assert len(set(conn.requests)) == len(conn.requests)
    records, _ = stage.read_journal(tmp_path)
    assert not any(r["event"] == "unclean_termination" for r in records)
    assert records[-1]["event"] == "finish"


def test_run_blocked_exit_and_resume_never_connect(tmp_path: Path, monkeypatch: Any) -> None:
    approve_fixture(tmp_path, monkeypatch)
    real_client = stage.Client
    conn = Connection([Response(403)])
    monkeypatch.setattr(stage, "Client", lambda journal, keys: real_client(
        journal, keys, connect=lambda: conn, pause=lambda _: None,
    ))
    for resume in (False, True):
        with pytest.raises(SystemExit) as stopped:
            stage.run(tmp_path, resume)
        assert stopped.value.code == 2
    assert len(conn.requests) == 1


def test_new_run_cannot_self_approve_another_candidate_list(tmp_path: Path, monkeypatch: Any) -> None:
    approve_fixture(tmp_path, monkeypatch)
    stage.CANDIDATES.write_bytes(stage.encoded({
        "window_start": stage.START, "cutoff_exclusive": stage.CUTOFF,
        "candidates": [candidate("ETHUSDT")],
    }))
    monkeypatch.setattr(stage.http.client, "HTTPSConnection", lambda *args, **kwargs: pytest.fail("Network"))
    with pytest.raises(ValueError, match="operator approval"):
        stage.run(tmp_path, False)
    assert not list(tmp_path.glob("journal-*.jsonl"))


def test_resume_refuses_a_directory_without_a_journal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing journal"):
        stage.run(tmp_path, True)
    assert not (tmp_path / "plan.json").exists()
    assert not list(tmp_path.glob("journal-*.jsonl"))


def test_atomic_plan_publication_survives_interruption(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "plan.json"

    def interrupt_fsync(descriptor: int) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(stage.os, "fsync", interrupt_fsync)
        with pytest.raises(KeyboardInterrupt):
            stage.save_new(path, b"test plan\n")
    assert not path.exists()
    saved = list(tmp_path.glob(".plan.json.*.tmp"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"test plan\n"
    stage.save_new(path, b"test plan\n")
    assert path.read_bytes() == b"test plan\n"
    with pytest.raises(FileExistsError):
        stage.save_new(path, b"replacement")


def test_resume_recovers_daily_file_saved_before_journal_pin(tmp_path: Path, monkeypatch: Any) -> None:
    approve_fixture(tmp_path, monkeypatch)
    real_client = stage.Client
    conn = Connection([Response(404) for _ in range(500)])
    monkeypatch.setattr(stage, "Client", lambda journal, keys: real_client(
        journal, keys, connect=lambda: conn, pause=lambda _: None,
    ))
    original_append = stage.Journal.append

    def interrupt_plan(journal: Any, event: str, **fields: Any) -> Any:
        if event == "daily_plan":
            raise KeyboardInterrupt
        return original_append(journal, event, **fields)

    monkeypatch.setattr(stage.Journal, "append", interrupt_plan)
    with pytest.raises(KeyboardInterrupt):
        stage.run(tmp_path, False)
    assert (tmp_path / "daily-plan.json").exists()
    assert len(conn.requests) == 168
    monkeypatch.setattr(stage.Journal, "append", original_append)
    stage.run(tmp_path, True)
    assert len(conn.requests) == 168 + 31 * 7
    records, _ = stage.read_journal(tmp_path)
    assert sum(r["event"] == "daily_plan" for r in records) == 1
    assert stage.validate(tmp_path, tmp_path / "summary.json")["run_status"] == "FINISHED"


def test_resume_validates_existing_log_before_connecting(tmp_path: Path, monkeypatch: Any) -> None:
    approve_fixture(tmp_path, monkeypatch)
    stage.initialize(tmp_path)
    journal = stage.Journal(tmp_path)
    intent = journal.append("intent", method="HEAD", url=URL, retry_of=None)
    journal.append("response", attempt=intent["seq"], method="GET", url=URL + ".CHECKSUM",
                   http_status=200, content_length="100")
    journal.close()
    original = journal.path.read_bytes()
    monkeypatch.setattr(stage.http.client, "HTTPSConnection", lambda *args, **kwargs: pytest.fail("Network"))
    with pytest.raises(ValueError, match="response linkage"):
        stage.run(tmp_path, True)
    assert journal.path.read_bytes() == original
    assert len(list(tmp_path.glob("journal-*.jsonl"))) == 1
