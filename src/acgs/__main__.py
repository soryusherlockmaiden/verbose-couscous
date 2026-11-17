import argparse
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from acgs.capture import capture_due
from acgs.clock import FixedClock, SystemClock
from acgs.config import ConfigError, load_config
from acgs.ledger import open_ledger
from acgs.market_data import fetch_public
from acgs.smoke import run_smoke


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m acgs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/acgs.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "smoke", help="Write or replay the deterministic offline state fixture"
    )
    capture = commands.add_parser("capture", help="Capture public exchange data")
    capture.add_argument("--watch", action="store_true", help="Poll for due snapshots")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        with open_ledger(args.db) as connection:
            if args.command == "smoke":
                clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
                created = run_smoke(connection, config, clock)
                print(
                    f"OFFLINE smoke {'recorded' if created else 'replayed'} {config.digest()}"
                )
                return 0
            while True:
                outcomes = Counter(
                    capture_due(
                        connection,
                        config.capture,
                        SystemClock(),
                        fetch_public,
                        time.sleep,
                    )
                )
                if outcomes:
                    summary = " ".join(
                        f"{key}={value}" for key, value in sorted(outcomes.items())
                    )
                    print(f"Capture {summary}", flush=True)
                if "ACCESS_RESTRICTED" in outcomes:
                    print(
                        "Public access restricted (HTTP 403/451). Operator review required.",
                        file=sys.stderr,
                    )
                    return 1
                if not args.watch:
                    return int(any(key != "CAPTURED" for key in outcomes))
                time.sleep(30)
    except ConfigError as error:
        print(f"Configuration refused: {error}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("Cannot open or update the SQLite ledger.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
