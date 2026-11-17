import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from acgs.clock import utc_iso
from acgs.config import Config

# Each entry upgrades the schema by one version; never edit a released entry.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE config_versions (
            digest TEXT PRIMARY KEY, config_json TEXT NOT NULL
        )""",
        """CREATE TABLE state_events (
            event_id TEXT PRIMARY KEY, config_digest TEXT NOT NULL,
            event_time TEXT NOT NULL, kind TEXT NOT NULL,
            FOREIGN KEY (config_digest) REFERENCES config_versions (digest)
        )""",
        """CREATE TABLE capture_attempts (
            id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, slot TEXT NOT NULL,
            requested_at TEXT NOT NULL, received_at TEXT NOT NULL,
            status TEXT NOT NULL, http_status INTEGER, retry_at TEXT,
            payload BLOB, sha256 TEXT, provenance TEXT,
            CHECK (
                (status = 'CAPTURED' AND payload IS NOT NULL AND sha256 IS NOT NULL
                 AND provenance IS NOT NULL AND provenance = 'VERIFIED')
                OR
                (status != 'CAPTURED' AND payload IS NULL AND sha256 IS NULL
                 AND provenance IS NULL)
            )
        )""",
        """CREATE UNIQUE INDEX capture_once ON capture_attempts (source_url, slot)
           WHERE status = 'CAPTURED'""",
        """CREATE INDEX capture_retry ON capture_attempts (retry_at)
           WHERE retry_at IS NOT NULL""",
        """CREATE INDEX capture_latest ON capture_attempts (source_url, slot, id)""",
    ),
    (
        # NULL payload_encoding means the raw response bytes (version 1 rows).
        "ALTER TABLE capture_attempts ADD COLUMN payload_encoding TEXT",
        # An HTTP 403/451 stays latched until an operator records a review.
        "ALTER TABLE capture_attempts ADD COLUMN cleared_at TEXT",
        """CREATE INDEX capture_restriction_open ON capture_attempts (id)
           WHERE status = 'ACCESS_RESTRICTED' AND cleared_at IS NULL""",
    ),
)
SCHEMA_VERSION = len(MIGRATIONS)


class LedgerStateError(Exception):
    """Stored state does not match the expected record."""


@contextmanager
def open_ledger(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not 0 <= version <= SCHEMA_VERSION:
                raise sqlite3.DatabaseError("Unsupported ledger schema version")
            for statements in MIGRATIONS[version:]:
                for statement in statements:
                    connection.execute(statement)
            if version != SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        yield connection
    finally:
        connection.close()


def insert_state_event(
    connection: sqlite3.Connection,
    config: Config,
    event_id: str,
    instant: datetime,
    kind: str,
) -> bool:
    """Insert an audit event; the caller owns the transaction."""
    digest = config.digest()
    connection.execute(
        "INSERT INTO config_versions VALUES (?, ?) ON CONFLICT DO NOTHING",
        (digest, config.canonical_json()),
    )
    inserted = connection.execute(
        "INSERT INTO state_events VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (event_id, digest, utc_iso(instant), kind),
    )
    return inserted.rowcount == 1


def record_smoke(
    connection: sqlite3.Connection, config: Config, instant: datetime
) -> bool:
    """Record the offline smoke fixture, or verify the stored one on replay."""
    event_id = f"offline-smoke:{config.digest()}"
    with connection:
        created = insert_state_event(
            connection, config, event_id, instant, "OFFLINE_SMOKE"
        )
    if created:
        return True
    row = connection.execute(
        """SELECT e.event_time, e.kind, v.config_json FROM state_events AS e
           JOIN config_versions AS v ON v.digest = e.config_digest
           WHERE e.event_id = ?""",
        (event_id,),
    ).fetchone()
    try:
        matches = (
            row is not None
            and row[1] == "OFFLINE_SMOKE"
            and row[2] == config.canonical_json()
            and datetime.fromisoformat(row[0]) == instant
        )
    except ValueError:
        matches = False
    if not matches:
        raise LedgerStateError("stored offline smoke fixture differs from the expected state")
    return False
