"""Register an operator-saved O2 notice without network access."""

import argparse
import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DISCOVERY = REPO / "evidence/m2/o2-discovery.json"
LEDGER = REPO / "evidence/m2/o2-notices.json"
COPIES = REPO / "data/o2-inventory/notices-operator"
SYMBOLS = {"BTCUSDT", "ETHUSDT", "DGBUSDT"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def local_file(path: Path, suffixes: set[str]) -> Path:
    resolved = path.resolve(strict=True)
    root = COPIES.resolve()
    if not root.is_relative_to(REPO / "data") or not resolved.is_relative_to(root):
        raise ValueError("Input must be under data/o2-inventory/notices-operator")
    if resolved.suffix.lower() not in suffixes or not resolved.is_file():
        raise ValueError(f"Expected a local file with one of these extensions: {sorted(suffixes)}")
    return resolved


def saved_utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Supply the save instant with an explicit UTC offset")
    return stamp


def publication_day(value: str) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|\+00:00)?)?", value):
        raise ValueError("Expected the displayed publication date or date/time")
    return datetime.fromisoformat(value).date()


def reviewed_facts(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = read_json(local_file(path, {".json"}))
    if not isinstance(value, list):
        raise ValueError("Reviewed facts must be a JSON array")
    for row in value:
        if not isinstance(row, dict) or row.get("symbol") not in SYMBOLS:
            raise ValueError("Every reviewed fact must identify an approved probe symbol")
        if not isinstance(row.get("kind"), str) or not row["kind"]:
            raise ValueError("Every reviewed fact needs a kind")
    return value


def register(url: str, source: Path, saved_at: str, published_display: str, facts_file: Path | None) -> str:
    discovery = read_json(DISCOVERY)
    approved = {row["url"] for row in discovery["selected_candidates"]}
    if url not in approved:
        raise ValueError("Expected an exact O2 discovery URL")
    published = publication_day(published_display)
    cutoff = saved_utc(discovery["cutoff_exclusive"])
    saved = saved_utc(saved_at)
    if published >= cutoff.date():
        raise ValueError("Publication is outside the O2 cutoff")
    if saved.date() < published:
        raise ValueError("Save instant precedes the publication date")
    ledger = read_json(LEDGER)
    if ledger["cutoff_exclusive"] != discovery["cutoff_exclusive"]:
        raise ValueError("Notice evidence cutoff differs from O2 discovery")
    matching = [row for row in ledger["notices"] if row["original_url"] == url]
    if len(matching) != 1 or matching[0]["wayback_status"] not in {"NOT_ARCHIVED", "BLOCKED"}:
        raise ValueError("Expected exactly one unresolved Wayback record for the selected URL")
    facts = reviewed_facts(facts_file)
    source = local_file(source, {".html", ".htm", ".pdf", ".mhtml", ".mht"})
    raw = source.read_bytes()
    if not raw.strip() or (source.suffix.lower() == ".pdf" and not raw.startswith(b"%PDF-")):
        raise ValueError("Expected a nonempty browser copy in the selected format")
    digest = hashlib.sha256(raw).hexdigest()
    item = {
        "original_url": url,
        "provenance": "operator-saved browser copy",
        "saved_at": saved.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "published_display": published_display,
        "sha256": digest,
        "size_bytes": len(raw),
        "raw_path": source.relative_to(REPO).as_posix(),
        "facts": facts,
        "facts_status": "REVIEWED_TRANSCRIPTION" if facts else "PENDING_REVIEW",
    }
    row = matching[0]
    old = row.get("operator_copy")
    if old == item:
        return digest
    if old is not None and old != {**item, "facts": [], "facts_status": "PENDING_REVIEW"}:
        raise ValueError("Existing operator evidence differs; review the replacement explicitly")
    row["operator_copy"] = item
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=LEDGER.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(ledger, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    try:
        temporary.replace(LEDGER)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--saved-at", required=True, help="UTC instant when the browser copy was saved")
    parser.add_argument("--published-display", required=True, help="Publication date or time as printed on the copy")
    parser.add_argument("--facts", type=Path, help="Optional operator-reviewed JSON array under the same directory")
    args = parser.parse_args()
    try:
        digest = register(args.url, args.file, args.saved_at, args.published_display, args.facts)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"Notice import refused: {error}\n")
    print(f"Registered operator copy SHA-256 {digest}")


if __name__ == "__main__":
    main()
