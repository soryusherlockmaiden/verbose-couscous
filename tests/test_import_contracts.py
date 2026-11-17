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


def layer_of(relative_path: Path) -> str:
    return relative_path.with_suffix("").parts[0]


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


def reachable_layers(start: str, graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    pending = [start]
    while pending:
        layer = pending.pop()
        if layer not in reached:
            reached.add(layer)
            pending.extend(graph.get(layer, set()))
    return reached


def decision_dependency_violations(sources: dict[Path, str]) -> list[str]:
    graph: dict[str, set[str]] = {}
    for path, source in sources.items():
        imports = imported_modules(source)
        graph.setdefault(layer_of(path), set()).update(
            name.split(".")[1] for name in imports if name.startswith("acgs.")
        )

    # Importing a decision module also runs the acgs package initializer.
    roots = (DECISION_LAYERS & graph.keys()) | {"__init__"}
    dependencies: set[str] = set()
    for root in roots:
        dependencies.update(reachable_layers(root, graph))
    problems = [
        f"Forbidden dependency: {layer}" for layer in sorted(CAPABILITY_LAYERS & dependencies)
    ]
    for path, source in sources.items():
        if layer_of(path) in dependencies:
            problems.extend(
                f"{path.as_posix()}: {problem}" for problem in decision_violations(source)
            )
    return problems


def test_decision_dependencies_are_deterministic_and_capability_free() -> None:
    sources = {path.relative_to(SOURCE): path.read_text(encoding="utf-8") for path in source_files()}
    assert decision_dependency_violations(sources) == []


def test_import_contract_detects_indirect_capability_access() -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import fetch_public\n",
        Path("helper.py"): "from acgs.market_data import fetch_public\n",
    }
    assert decision_dependency_violations(sources) == ["Forbidden dependency: market_data"]


@pytest.mark.parametrize(
    ("helper_source", "expected"),
    [
        ("from datetime import datetime\nread_clock = datetime.now\n", "datetime.now"),
        ("import time\nread_clock = time.time\n", "import time"),
    ],
)
def test_wall_clock_is_rejected_through_transitive_helpers(
    helper_source: str, expected: str
) -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import read_clock\n",
        Path("helper.py"): "from acgs.support.stamp import read_clock\n",
        Path("support/stamp.py"): helper_source,
    }
    assert decision_dependency_violations(sources) == [f"support/stamp.py: {expected}"]


def test_transitive_helpers_can_use_an_injected_clock() -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import stamp\n",
        Path("helper.py"): (
            "from acgs.clock import Clock\n"
            "def stamp(clock: Clock):\n"
            "    return clock.now()\n"
        ),
        Path("clock.py"): (SOURCE / "clock.py").read_text(encoding="utf-8"),
        Path("unrelated.py"): "from datetime import datetime\nstamp = datetime.now\n",
    }
    assert decision_dependency_violations(sources) == []


def test_wall_clock_is_rejected_through_the_package_initializer() -> None:
    sources = {
        Path("strategy.py"): "",
        Path("__init__.py"): "from acgs.helper import stamp\n",
        Path("helper.py"): "from datetime import datetime\nstamp = datetime.now()\n",
    }
    assert decision_dependency_violations(sources) == ["helper.py: datetime.now"]


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
        "from acgs.__main__ import SystemClock\n",
        "from acgs import __main__\n__main__.SystemClock()\n",
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
