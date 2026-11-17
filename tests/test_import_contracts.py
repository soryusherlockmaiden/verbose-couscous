"""M0 capability boundaries (T-13, T-16), checked by pytest in both native CI jobs."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "acgs"
SAFE_IMPORTS = {
    "argparse",
    "collections",
    "contextlib",
    "dataclasses",
    "datetime",
    "decimal",
    "email",
    "enum",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "pathlib",
    "re",
    "sqlite3",
    "sys",
    "time",
    "tomllib",
    "typing",
    "zlib",
}
PUBLIC_HTTP_IMPORTS = {"http.client", "urllib.error", "urllib.request"}
DECISION_LAYERS = {"strategy", "features", "universe", "research"}
CAPABILITY_LAYERS = {"market_data", "capture", "execution", "transfers", "__main__"}
# Decision code must be deterministic: no wall clock, process, system or I/O modules.
DECISION_SAFE_IMPORTS = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "itertools",
    "math",
    "re",
    "typing",
}
DYNAMIC_CODE = {"__import__", "eval", "exec"}
WALL_CLOCK_CALLS = {"now", "utcnow", "today"}


def source_files() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def layer_of(path: Path) -> str:
    return path.relative_to(SOURCE).with_suffix("").parts[0]


def imported_modules(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "acgs":
                names.update(f"acgs.{alias.name}" for alias in node.names)
            else:
                names.add(node.module)
    return names


def module_violations(source: str) -> list[str]:
    """Rules for every module, so the layer graph below sees every acgs import."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level:
            problems.append("relative import")
        elif isinstance(node, ast.Import):
            problems.extend(
                f"import {alias.name} (use `from acgs... import ...`)"
                for alias in node.names
                if alias.name.split(".")[0] == "acgs"
            )
        elif isinstance(node, ast.Name) and node.id in DYNAMIC_CODE:
            problems.append(node.id)
    return problems


def decision_violations(source: str) -> list[str]:
    """Extra rules for decision layers: no capabilities and no wall-clock reads."""
    tree = ast.parse(source)
    problems = module_violations(source)
    clock_classes: set[str] = set()  # names bound to datetime.datetime or datetime.date
    clock_modules: set[str] = set()  # names bound to the datetime module
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in DECISION_SAFE_IMPORTS:
                    problems.append(f"import {alias.name}")
                elif alias.name == "datetime":
                    clock_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root != "acgs" and root not in DECISION_SAFE_IMPORTS:
                problems.append(f"from {node.module} import ...")
            for alias in node.names:
                if alias.name == "SystemClock":
                    problems.append("SystemClock")
                if node.module == "datetime" and alias.name in {"datetime", "date"}:
                    clock_classes.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr == "SystemClock":
            problems.append("SystemClock")
        elif node.attr in WALL_CLOCK_CALLS:
            target = node.value
            if isinstance(target, ast.Name) and target.id in clock_classes:
                problems.append(f"{target.id}.{node.attr}")
            elif (
                isinstance(target, ast.Attribute)
                and target.attr in {"datetime", "date"}
                and isinstance(target.value, ast.Name)
                and target.value.id in clock_modules
            ):
                problems.append(f"{target.value.id}.{target.attr}.{node.attr}")
    return problems


def test_m0_has_only_public_http_and_no_order_or_transfer_adapter() -> None:
    for path in source_files():
        parts = path.relative_to(SOURCE).with_suffix("").parts
        assert {"execution", "transfers"}.isdisjoint(parts)
        source = path.read_text(encoding="utf-8")
        assert not module_violations(source), (path.name, module_violations(source))
        for name in imported_modules(source):
            root = name.split(".")[0]
            common = root in SAFE_IMPORTS or root == "acgs" or name == "urllib.parse"
            public_http = parts == ("market_data",) and name in PUBLIC_HTTP_IMPORTS
            assert common or public_http, (path.name, name)


def test_decision_layers_are_deterministic_and_capability_free() -> None:
    for path in source_files():
        if layer_of(path) in DECISION_LAYERS:
            source = path.read_text(encoding="utf-8")
            assert not decision_violations(source), (path.name, decision_violations(source))


def reachable_layers(start: str, graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    pending = [start]
    while pending:
        layer = pending.pop()
        if layer not in reached:
            reached.add(layer)
            pending.extend(graph.get(layer, set()))
    return reached


def test_strategy_and_research_cannot_import_capabilities_transitively() -> None:
    graph: dict[str, set[str]] = {}
    for path in source_files():
        imports = imported_modules(path.read_text(encoding="utf-8"))
        graph.setdefault(layer_of(path), set()).update(
            name.split(".")[1] for name in imports if name.startswith("acgs.")
        )
    for layer in DECISION_LAYERS:
        assert CAPABILITY_LAYERS.isdisjoint(reachable_layers(layer, graph))


def test_import_contract_detects_indirect_capability_access() -> None:
    graph = {"strategy": {"helper"}, "helper": {"market_data"}}
    assert CAPABILITY_LAYERS & reachable_layers("strategy", graph) == {"market_data"}


@pytest.mark.parametrize(
    "source",
    [
        "import acgs\nacgs.market_data.fetch_public\n",
        "import acgs.config\nacgs.market_data.fetch_public\n",
        "from .market_data import fetch_public\n",
        "__import__('acgs.market_data')\n",
        "import sys\nsys.modules['acgs.market_data']\n",
        "import importlib\nimportlib.import_module('acgs.market_data')\n",
        "import time\ntime.time()\n",
        "from datetime import UTC, datetime\ndatetime.now(UTC)\n",
        "from datetime import datetime as moment\nmoment.utcnow()\n",
        "import datetime\ndatetime.date.today()\n",
        "from acgs.clock import SystemClock\n",
        "from acgs import clock\nclock.SystemClock()\n",
    ],
)
def test_decision_rules_reject_capability_and_wall_clock_bypasses(source: str) -> None:
    assert decision_violations(source)


def test_decision_rules_allow_an_injected_clock() -> None:
    source = (
        "from datetime import UTC, datetime\n\n"
        "from acgs.clock import Clock\n\n\n"
        "def stamp(clock: Clock) -> datetime:\n"
        "    return clock.now().astimezone(UTC)\n"
    )
    assert decision_violations(source) == []
