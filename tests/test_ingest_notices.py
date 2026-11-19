import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evidence/m2/ingest_notices.py"
spec = spec_from_file_location("ingest_notices", SCRIPT)
assert spec is not None and spec.loader is not None
ingest = module_from_spec(spec)
spec.loader.exec_module(ingest)

URL = "https://www.binance.com/en/support/announcement/detail/example"
EXPANSION_URL = "https://www.binance.com/en/support/announcement/detail/expansion"
SAVE = "2026-11-18T22:18:25Z"
PUBLISHED = "2024-03-25 04:40"


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, Any]]:
    discovery = tmp_path / "o2-discovery.json"
    ledger = tmp_path / "o2-notices.json"
    copies = tmp_path / "data/o2-inventory/notices-operator"
    copies.mkdir(parents=True)
    monkeypatch.setattr(ingest, "REPO", tmp_path)
    monkeypatch.setattr(ingest, "DISCOVERY", discovery)
    monkeypatch.setattr(ingest, "LEDGER", ledger)
    monkeypatch.setattr(ingest, "COPIES", copies)
    save_list = tmp_path / "o2-operator-save-list.md"
    candidates = tmp_path / "o2-candidates.json"
    monkeypatch.setattr(ingest, "SAVE_LIST", save_list)
    monkeypatch.setattr(ingest, "CANDIDATES", candidates)
    before = {
        "cutoff_exclusive": "2025-01-01T00:00:00Z",
        "notices": [{"original_url": URL, "wayback_status": "NOT_ARCHIVED", "operator_copy": None}],
    }
    ledger.write_text(json.dumps(before), encoding="utf-8")
    discovery.write_text(json.dumps({
        "cutoff_exclusive": before["cutoff_exclusive"], "selected_candidates": [{"url": URL}],
    }), encoding="utf-8")
    save_list.write_text(f"| [Expansion]({EXPANSION_URL}) | Save as PDF |\n", encoding="utf-8")
    candidates.write_text(json.dumps({
        "cutoff_exclusive": before["cutoff_exclusive"], "candidates": [{"symbol": "EXAMPLEUSDT"}],
    }), encoding="utf-8")
    source = copies / "notice.pdf"
    source.write_bytes(b"%PDF-1.7\nsynthetic fixture")
    return source, ledger, before


def test_register_hash_and_idempotent_review(files: tuple[Path, Path, dict[str, Any]]) -> None:
    source, ledger, _ = files
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert ingest.register(URL, source, SAVE, PUBLISHED, None) == digest
    row = json.loads(ledger.read_bytes())["notices"][0]
    assert row["wayback_status"] == "NOT_ARCHIVED"
    assert row["operator_copy"]["sha256"] == digest
    assert row["operator_copy"]["size_bytes"] == source.stat().st_size
    assert row["operator_copy"]["provenance"] == "operator-saved browser copy"
    assert row["operator_copy"]["facts_status"] == "PENDING_REVIEW"
    facts = source.with_suffix(".json")
    facts.write_text('[{"symbol":"DGBUSDT","kind":"CLOSE_ONLY"}]', encoding="utf-8")
    ingest.register(URL, source, SAVE, PUBLISHED, facts)
    reviewed = ledger.read_bytes()
    ingest.register(URL, source, SAVE, PUBLISHED, facts)
    assert ledger.read_bytes() == reviewed
    assert json.loads(reviewed)["notices"][0]["operator_copy"]["facts_status"] == "REVIEWED_TRANSCRIPTION"
    source.write_bytes(b"%PDF-1.7\nreplacement")
    with pytest.raises(ValueError, match="Existing operator evidence differs"):
        ingest.register(URL, source, SAVE, PUBLISHED, facts)
    assert ledger.read_bytes() == reviewed


@pytest.mark.parametrize(("url", "stamp", "publication"), [
    (URL + "?different", SAVE, PUBLISHED),
    (URL, "2026-11-19T05:18:25+07:00", PUBLISHED),
    (URL, SAVE, "2025-01-01"),
    (URL, SAVE, "2025-01-01 00:00"),
])
def test_scope_checks_before_document_read(
    files: tuple[Path, Path, dict[str, Any]], url: str, stamp: str, publication: str,
) -> None:
    source, ledger, before = files
    source.unlink()
    with pytest.raises(ValueError):
        ingest.register(url, source, stamp, publication, None)
    assert json.loads(ledger.read_bytes()) == before


def test_source_outside_operator_directory(files: tuple[Path, Path, dict[str, Any]]) -> None:
    _, ledger, before = files
    outside = ledger.with_suffix(".pdf")
    outside.write_bytes(b"%PDF-1.7\nsynthetic fixture")
    with pytest.raises(ValueError, match="Input must be under"):
        ingest.register(URL, outside, SAVE, PUBLISHED, None)
    assert json.loads(ledger.read_bytes()) == before


def test_expansion_creates_record_and_rejects_replacement(files: tuple[Path, Path, dict[str, Any]]) -> None:
    source, ledger, before = files
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, None) == digest
    initial = json.loads(ledger.read_bytes())["notices"]
    assert initial[0] == before["notices"][0]
    assert initial[1]["original_url"] == EXPANSION_URL
    assert initial[1]["source_inventory"] == "o2-operator-save-list.md"
    assert initial[1]["wayback_status"] == "NOT_CHECKED"
    assert initial[1]["operator_copy"]["facts_status"] == "PENDING_REVIEW"

    facts = source.with_suffix(".json")
    facts.write_text('[{"symbol":"EXAMPLEUSDT","kind":"LISTING"}]', encoding="utf-8")
    assert ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, facts) == digest
    reviewed = ledger.read_bytes()
    assert ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, facts) == digest
    assert json.loads(reviewed)["notices"][1]["operator_copy"]["facts_status"] == "REVIEWED_TRANSCRIPTION"

    facts.write_text('[{"symbol":"EXAMPLEUSDT","kind":"DELISTING"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="Existing operator evidence differs"):
        ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, facts)
    assert ledger.read_bytes() == reviewed


@pytest.mark.parametrize(("url", "published", "error"), [
    (EXPANSION_URL + "/", PUBLISHED, "exact O2"),
    (EXPANSION_URL + "?hl=en", PUBLISHED, "exact O2"),
    (EXPANSION_URL.replace("/detail/", "/list/"), PUBLISHED, "exact O2"),
    (EXPANSION_URL, "2025-01-01T00:00:00Z", "outside the O2 cutoff"),
    (EXPANSION_URL, PUBLISHED, "symbol in the selected O2 scope"),
])
def test_expansion_scope_before_reading_copy(
    files: tuple[Path, Path, dict[str, Any]], url: str, published: str, error: str,
) -> None:
    source, ledger, before = files
    source.unlink()
    facts = source.with_suffix(".json")
    facts.write_text('[{"symbol":"DGBUSDT","kind":"LISTING"}]', encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        ingest.register(url, source, SAVE, published, facts)
    assert json.loads(ledger.read_bytes()) == before


def test_expansion_cutoff_must_match_legacy_scope(files: tuple[Path, Path, dict[str, Any]]) -> None:
    source, ledger, before = files
    source.unlink()
    ingest.CANDIDATES.write_text('{"cutoff_exclusive":"2026-01-01T00:00:00Z"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Candidate cutoff differs"):
        ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, None)
    assert json.loads(ledger.read_bytes()) == before


def test_expansion_refuses_overlapping_ledger_write(files: tuple[Path, Path, dict[str, Any]]) -> None:
    source, ledger, before = files
    lock = source.parent / ".ingest.lock"
    lock.touch()
    with pytest.raises(ValueError, match="Another notice import is in progress"):
        ingest.register(EXPANSION_URL, source, SAVE, PUBLISHED, None)
    assert lock.exists()
    assert json.loads(ledger.read_bytes()) == before
