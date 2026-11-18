import hashlib
import json
from pathlib import Path
from typing import Any

import ingest_notices as ingest
import pytest

URL = "https://www.binance.com/en/support/announcement/detail/example"
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
    before = {
        "cutoff_exclusive": "2025-01-01T00:00:00Z",
        "notices": [{"original_url": URL, "wayback_status": "NOT_ARCHIVED", "operator_copy": None}],
    }
    ledger.write_text(json.dumps(before), encoding="utf-8")
    discovery.write_text(json.dumps({
        "cutoff_exclusive": before["cutoff_exclusive"], "selected_candidates": [{"url": URL}],
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
