"""Local composition root for deterministic historical reporting and replay."""

from argparse import ArgumentParser, Namespace
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from os import link
from os import name as os_name
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, get_args

from acgs.broker_sim import Action, Intent, ReplayPlan, replay
from acgs.clock import FixedClock
from acgs.eligibility import Side
from acgs.feasibility import feasibility
from acgs.history_io import integer, load_history, number, read_json
from acgs.history_seal import Window, registered_seal
from acgs.pit import Filters, FilterWithdrawal, Instrument
from acgs.sim_config import EPOCH, Policy, StopBasis, canonical, digest, instant_ms


def _policy(path: Path) -> Policy:
    raw = read_json(path)
    if raw.keys() != {field.name for field in fields(Policy)}:
        raise ValueError("M1 policy fields do not match its schema")
    values: dict[str, Any] = {}
    for field in fields(Policy):
        value = raw[field.name]
        if field.type is Decimal:
            value = number(value)
        elif get_args(field.type) == (Decimal, Ellipsis):
            if not isinstance(value, list):
                raise ValueError(f"Expected a list of decimal strings for {field.name}")
            value = tuple(number(x) for x in value)
        elif get_args(field.type) == (int, Ellipsis):
            if not isinstance(value, list) or any(type(x) is not int for x in value):
                raise ValueError(f"Expected integral list for {field.name}")
            value = tuple(value)
        elif field.type is StopBasis:
            value = StopBasis(value)
        elif type(value) is not field.type:
            raise ValueError(f"Wrong policy field type for {field.name}")
        values[field.name] = value
    return Policy(**values)


def arguments(commands: Any) -> None:
    for name, help_text in (
        ("historical", "Validate and summarize a pinned offline historical dataset"),
        ("feasibility", "Generate the 50 USDT offline feasibility report"),
        ("replay", "Simulate a versioned sequence of offline intents"),
    ):
        parser: ArgumentParser = commands.add_parser(name, help=help_text)
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--assets", type=Path, required=True)
        parser.add_argument("--capture-db", type=Path)
        parser.add_argument("--out", type=Path)
        if name != "historical":
            parser.add_argument("--policy", type=Path, required=True)
            parser.add_argument("--end", required=True)
            parser.add_argument("--start", required=True)
        if name == "replay":
            parser.add_argument("--plan", type=Path, required=True)


def _parse_time(value: str) -> int:
    return instant_ms(datetime.fromisoformat(value))


def _plan(raw: dict[str, Any], inst: Instrument, start: int) -> ReplayPlan:
    if not isinstance(raw["candidate_version"], str) or not isinstance(raw["intents"], list):
        raise ValueError("Replay plan requires a version string and an intent list")
    intents = tuple(
        Intent(
            _parse_time(row["time"]), _parse_time(row["available"]),
            Action(row["action"]), Side[row["side"]],
            number(row.get("distance", "0")), number(row["quantity"]) if "quantity" in row else None,
        )
        for row in raw["intents"]
    )
    return ReplayPlan(
        raw["candidate_version"], inst, start, integer(raw["interval_ms"]), number(raw["equity"]),
        number(raw["risk_pct"]), integer(raw["leverage"]), intents,
        number(raw["halt_level"]) if "halt_level" in raw else None,
    )


def run_research(args: Namespace) -> int:
    if args.command != "historical":
        window = Window(_parse_time(args.start), _parse_time(args.end))
        registered_seal().require_unsealed(window, "M1 research request")
    loaded = load_history(args.manifest, args.assets, args.capture_db)
    market = loaded.market
    if args.command == "historical":
        history: dict[str, Any] = {}
        for inst in sorted(market.listings):
            bars = market.bars.get(inst, ())
            mark_keys = {(b.start, b.end) for b in bars if b.is_mark}
            contract_keys = {(b.start, b.end) for b in bars if not b.is_mark}
            history[inst.id] = {
                "contract_candles": len(contract_keys), "mark_candles": len(mark_keys),
                "contract_intervals_without_matching_mark": len(contract_keys - mark_keys),
                "settlements": len(market.settlements.get(inst, ())),
                "settlements_with_mark": sum(f.mark is not None for f in market.settlements.get(inst, ())),
                "listing_observations": len(market.listings[inst]),
                # Withdrawals are metadata observations too, but never usable filters.
                "filter_observations": len(market.filters.get(inst, ())),
                "filter_snapshots": sum(isinstance(f, Filters) for f in market.filters.get(inst, ())),
                "filter_withdrawals": sum(
                    isinstance(f, FilterWithdrawal) for f in market.filters.get(inst, ())
                ),
                "maintenance_observations": len(market.maintenance.get(inst, ())),
                "schedule_observations": len(market.schedules.get(inst, ())),
                "spread_observations": len(market.spreads.get(inst, ())),
            }
        result = {"dataset_digest": loaded.digest, "coverage": history}
    else:
        policy = _policy(args.policy)
        start = _parse_time(args.start)
        end = FixedClock((EPOCH + timedelta(milliseconds=_parse_time(args.end))).astimezone(UTC))
        if args.command == "feasibility":
            result = feasibility(market, loaded.digest, policy, start, end)
        else:
            raw = read_json(args.plan)
            replay_instrument: Instrument | None = next(
                (i for i in market.listings if i.id == raw["instrument_id"]), None,
            )
            if replay_instrument is None:
                raise ValueError("Replay plan references an unavailable instrument epoch")
            result = asdict(replay(
                market, loaded.digest, policy, end, _plan(raw, replay_instrument, start),
            ))
    output = canonical(result) + "\n"
    if args.out is None:
        print(output, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        output_bytes = output.encode("utf-8")
        with NamedTemporaryFile(dir=args.out.parent, mode="wb", delete=False) as stream:
            scratch = Path(stream.name)
            stream.write(output_bytes)
        try:
            # Publish complete bytes atomically without replacing another writer's evidence.
            if os_name == "nt":
                scratch.rename(args.out)
            else:
                link(scratch, args.out)
        except FileExistsError:
            if args.out.read_bytes() != output_bytes:
                raise ValueError("Existing evidence differs; use a new output path") from None
        finally:
            scratch.unlink(missing_ok=True)
        print(f"OFFLINE {args.command} dataset={loaded.digest} output={digest(result)}")
    return 0
