import argparse
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import DecimalException
from pathlib import Path
from zipfile import BadZipFile

from acgs.capture import (
    BLOCKED,
    RESTRICTED,
    capture_due,
    clear_access_restriction,
    slot_summary,
)
from acgs.clock import FixedClock
from acgs.config import ConfigError
from acgs.config_io import load_config
from acgs.history_seal import AccessRefused
from acgs.ledger import open_ledger
from acgs.ledger_types import LedgerStateError
from acgs.market_data import fetch_public
from acgs.research_io import arguments, run_research
from acgs.smoke import run_smoke
from acgs.wallclock import SystemClock


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m acgs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/acgs.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "smoke", help="Write or replay the deterministic offline state fixture"
    )
    capture = commands.add_parser("capture", help="Capture public exchange data")
    options = capture.add_mutually_exclusive_group()
    options.add_argument("--watch", action="store_true", help="Poll for due snapshots")
    options.add_argument(
        "--clear-restriction",
        action="store_true",
        help="Record operator review of an HTTP 403/451 stop and clear it",
    )
    arguments(commands)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.command in {"historical", "feasibility", "replay"}:
            try:
                return run_research(args)
            except (ValueError, KeyError, TypeError, DecimalException, BadZipFile, OSError, sqlite3.Error) as error:
                print(f"OFFLINE historical operation refused: {error}", file=sys.stderr)
                return 1
        with open_ledger(args.db) as connection:
            if args.command == "smoke":
                clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
                created = run_smoke(connection, config, clock)
                print(
                    f"OFFLINE smoke {'recorded' if created else 'replayed'} {config.digest()}"
                )
                return 0
            if args.clear_restriction:
                cleared = clear_access_restriction(connection, config, SystemClock())
                print(f"Cleared {cleared} capture access restriction(s)")
                return 0
            latched = False
            while True:
                slots: list[str] = []
                outcomes = Counter(
                    capture_due(
                        connection,
                        config.capture,
                        SystemClock(),
                        fetch_public,
                        time.sleep,
                        report_slots=slots,
                    )
                )
                degraded = False
                for slot in slots:
                    line, incomplete = slot_summary(connection, slot)
                    print(line, flush=True)
                    degraded |= incomplete
                restricted = bool(outcomes.keys() & {RESTRICTED, BLOCKED})
                # While latched, each poll only re-reads the latch; log the state once.
                if outcomes and not (restricted and latched):
                    summary = " ".join(
                        f"{key}={value}" for key, value in sorted(outcomes.items())
                    )
                    print(f"Capture {summary}", flush=True)
                if restricted:
                    if not latched:
                        message = (
                            "Public access restricted (HTTP 403/451). Operator review "
                            "required; after review run `capture --clear-restriction`."
                        )
                        if args.watch:
                            message += " Capture is paused and sends no requests until then."
                        print(message, file=sys.stderr, flush=True)
                    if not args.watch:
                        return 1
                    latched = True
                elif latched:
                    print("Capture access restriction cleared; resuming.", flush=True)
                    latched = False
                if not args.watch:
                    return int(degraded or any(key != "CAPTURED" for key in outcomes))
                time.sleep(30)
    except AccessRefused as error:
        print(f"Capture refused: {error}", file=sys.stderr)
        return 1
    except ConfigError as error:
        print(f"Configuration refused: {error}", file=sys.stderr)
        return 2
    except LedgerStateError as error:
        print(f"Ledger state check failed: {error}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("Cannot open or update the SQLite ledger.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
