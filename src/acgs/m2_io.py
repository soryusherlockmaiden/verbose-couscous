"""Phase A OFFLINE fetch and coverage commands."""

import argparse
import sys
from collections.abc import Sequence
from decimal import DecimalException
from pathlib import Path
from zipfile import BadZipFile

from acgs.m2_coverage import coverage
from acgs.m2_data import Window, fetch_inputs, utc_ms
from acgs.research_io import _policy
from acgs.sim_config import canonical


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m acgs.m2_io")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--purpose", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="Fetch the approved Binance USD-M archive pins")
    audit = commands.add_parser("coverage", help="Audit approved history without running strategy")
    audit.add_argument("--start", required=True)
    audit.add_argument("--end", required=True)
    audit.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            report = fetch_inputs(args.config, args.manifest, args.assets, args.purpose)
        else:
            report = coverage(
                args.config, args.manifest, args.assets, args.purpose,
                Window(utc_ms(args.start), utc_ms(args.end)), _policy(args.policy),
            )
        print(canonical(report))
    except (ValueError, KeyError, TypeError, DecimalException, BadZipFile, OSError) as error:
        print(f"M2 OFFLINE data operation refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
