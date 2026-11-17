import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from acgs.config import Config

SCHEMA = (
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
    """CREATE INDEX capture_retry ON capture_attempts (retry_at) WHERE retry_at IS NOT NULL""",
    """CREATE INDEX capture_latest ON capture_attempts (source_url, slot, id)""",
)


@contextmanager
def open_ledger(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
            elif version != 1:
                raise sqlite3.DatabaseError("Unsupported ledger schema version")
        yield connection
    finally:
        connection.close()


def record_smoke(
    connection: sqlite3.Connection, config: Config, instant: datetime
) -> bool:
    digest = config.digest()
    with connection:
        connection.execute(
            "INSERT INTO config_versions VALUES (?, ?) ON CONFLICT DO NOTHING",
            (digest, config.canonical_json()),
        )
        inserted = connection.execute(
            """INSERT INTO state_events VALUES (?, ?, ?, 'OFFLINE_SMOKE')
               ON CONFLICT DO NOTHING""",
            (f"offline-smoke:{digest}", digest, instant.isoformat()),
        )
    return inserted.rowcount == 1
