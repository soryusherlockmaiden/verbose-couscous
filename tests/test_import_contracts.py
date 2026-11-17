"""M0 capability boundaries (T-13, T-16), checked by pytest in both native CI jobs.

These static AST rules are a cheap guardrail, not a proof of determinism: dynamic
access such as `getattr(datetime, "now")` or tuple unpacking can still evade them.
When decision code lands in M1, add a runtime determinism test that runs it under
different patched wall clocks (or with `time`/`datetime.now` patched to raise) and
asserts identical outputs.
"""

import ast
from collections.abc import Iterator
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
CAPABILITY_MODULES = {
    "acgs.market_data",
    "acgs.capture",
    "acgs.config_io",
    "acgs.execution",
    "acgs.ledger",
    "acgs.transfers",
    "acgs.wallclock",
    "acgs.__main__",
}
# Decision code must be deterministic: no wall clock, process, system or I/O modules.
DECISION_SAFE_IMPORTS = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "re",
    "typing",
}
DYNAMIC_CODE = {"__import__", "eval", "exec"}
DECISION_IO_BUILTINS = {"input", "open"}
WALL_CLOCK_CALLS = {"now", "utcnow", "today"}


def source_files() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def module_name(relative_path: Path) -> str:
    parts = relative_path.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("acgs", *parts))


def imported_modules(source: str, local_modules: set[str] | None = None) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            if local_modules is not None and node.module.partition(".")[0] == "acgs":
                # A from-import can load a submodule or read an attribute from the package.
                for alias in node.names:
                    child = f"{node.module}.{alias.name}"
                    if child in local_modules or child in CAPABILITY_MODULES:
                        names.add(child)
    return names


def module_violations(source: str) -> list[str]:
    """Rules for every module, so the graph below sees every acgs import."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append("relative import")
            if any(alias.name == "*" for alias in node.names):
                problems.append("wildcard import")
        elif isinstance(node, ast.Import):
            problems.extend(
                f"import {alias.name} (use `from acgs... import ...`)"
                for alias in node.names
                if alias.name.split(".")[0] == "acgs"
            )
        elif isinstance(node, ast.Name) and node.id in DYNAMIC_CODE:
            problems.append(node.id)
    return problems


def dotted_reference(node: ast.expr) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join((node.id, *reversed(parts)))
    return None


def clock_names(tree: ast.AST) -> set[str]:
    """Names and attributes annotated as the injected `Clock`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            annotated, annotation = node.arg, node.annotation
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name):
                annotated = target.id
            elif isinstance(target, ast.Attribute):
                annotated = target.attr
            else:
                continue
            annotation = node.annotation
        else:
            continue
        if isinstance(annotation, ast.Constant) and annotation.value == "Clock":
            names.add(annotated)
        elif annotation is not None:
            reference = dotted_reference(annotation)
            if reference is not None and reference.rpartition(".")[2] == "Clock":
                names.add(annotated)
    return names


def decision_violations(source: str) -> list[str]:
    """Apply the same purity checks to decision modules and reachable helpers."""
    tree = ast.parse(source)
    injected_clocks = clock_names(tree)
    problems = module_violations(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.partition(".")[0] not in DECISION_SAFE_IMPORTS:
                    problems.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.partition(".")[0]
            if root != "acgs" and root not in DECISION_SAFE_IMPORTS:
                problems.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Name) and node.id in DECISION_IO_BUILTINS:
            problems.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in WALL_CLOCK_CALLS:
            # `now`/`utcnow`/`today` read the wall clock on any datetime class or
            # instance; only the injected clock may provide the time.
            receiver = node.value
            injected = (
                isinstance(receiver, ast.Name) and receiver.id in injected_clocks
            ) or (isinstance(receiver, ast.Attribute) and receiver.attr in injected_clocks)
            if not injected:
                problems.append(f"{dotted_reference(receiver) or '<expression>'}.{node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "astimezone"
            and not node.args
            and not node.keywords
        ):
            problems.append("astimezone() without an explicit time zone")
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


def reachable_modules(roots: set[str], graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        module = pending.pop()
        if module not in reached:
            reached.add(module)
            pending.extend(graph.get(module, set()))
    return reached


def module_and_packages(name: str) -> Iterator[str]:
    while name:
        yield name
        name = name.rpartition(".")[0]


def decision_dependency_violations(sources: dict[Path, str]) -> list[str]:
    modules = {module_name(path): path for path in sources}
    known_names = {name for module in modules for name in module_and_packages(module)}

    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        dependencies: set[str] = set()
        for name in imported_modules(sources[path], known_names):
            if name.partition(".")[0] == "acgs":
                # Python executes parent package initializers when importing a module.
                dependencies.update(module_and_packages(name))
        # Stop at capability modules: importing one is already the violation to report.
        graph[module] = set() if module in CAPABILITY_MODULES else dependencies

    decision_modules = {
        module
        for module in modules
        if module != "acgs" and module.split(".", maxsplit=2)[1] in DECISION_LAYERS
    }
    reachable = reachable_modules(decision_modules | {"acgs"}, graph)
    problems = [
        f"Forbidden dependency: {module}"
        for module in sorted(CAPABILITY_MODULES & reachable)
    ]
    checked_modules = reachable - CAPABILITY_MODULES
    reachable_sources = {
        module: path for module, path in modules.items() if module in checked_modules
    }
    for path in reachable_sources.values():
        violations = decision_violations(sources[path])
        problems.extend(f"{path.as_posix()}: {problem}" for problem in violations)
    return problems


def test_decision_dependencies_are_deterministic_and_capability_free() -> None:
    sources = {
        path.relative_to(SOURCE): path.read_text(encoding="utf-8")
        for path in source_files()
    }
    assert decision_dependency_violations(sources) == []


def test_import_contract_detects_indirect_capability_access() -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import fetch_public\n",
        Path("helper.py"): "from acgs.market_data import fetch_public\n",
    }
    assert decision_dependency_violations(sources) == [
        "Forbidden dependency: acgs.market_data"
    ]


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


def test_decision_code_can_import_config_and_ledger_types() -> None:
    sources = {
        Path("strategy.py"): (
            "from acgs.config import Config\nfrom acgs.ledger_types import LedgerStateError\n"
        ),
        **{
            Path(name): (SOURCE / name).read_text(encoding="utf-8")
            for name in ("config.py", "ledger_types.py")
        },
    }
    assert decision_dependency_violations(sources) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from acgs.clock import datetime\nstamp = datetime.now()", "datetime.now"),
        ("from acgs import clock\nstamp = clock.datetime.now()", "clock.datetime.now"),
        (
            "from acgs.helper import DateTime\nstamp = DateTime.utcnow()",
            "DateTime.utcnow",
        ),
    ],
)
def test_wall_clock_is_rejected_through_reexports(source: str, expected: str) -> None:
    sources = {
        Path("strategy.py"): source,
        Path("clock.py"): (SOURCE / "clock.py").read_text(encoding="utf-8"),
        Path("helper.py"): "from acgs.clock import datetime as dt\nDateTime = dt",
    }
    assert decision_dependency_violations(sources) == [f"strategy.py: {expected}"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from pathlib import Path\nload = Path('state.txt').read_text",
            "from pathlib import ...",
        ),
        ("import sqlite3\nload = sqlite3.connect", "import sqlite3"),
        ("load = open", "open"),
        (
            "from acgs.ledger import open_ledger as load",
            "Forbidden dependency: acgs.ledger",
        ),
        (
            "from acgs.config_io import load_config as load",
            "Forbidden dependency: acgs.config_io",
        ),
    ],
)
def test_io_is_rejected_through_helpers(source: str, expected: str) -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import load",
        Path("helper.py"): source,
    }
    violations = decision_dependency_violations(sources)
    diagnostic = (
        expected
        if expected.startswith("Forbidden dependency:")
        else f"helper.py: {expected}"
    )
    assert violations == [diagnostic]


@pytest.mark.parametrize(
    "source",
    ["from acgs.support.symbols import symbol", "from acgs.support import symbols"],
)
def test_an_unimported_sibling_does_not_affect_the_imported_module(source: str) -> None:
    sources = {
        Path("strategy.py"): source,
        Path("support/symbols.py"): "symbol = 'BTCUSDT'",
        Path("support/fetch.py"): "from acgs.market_data import fetch_public",
    }
    assert decision_dependency_violations(sources) == []
    sources[Path("support/__init__.py")] = "from acgs.support import fetch"
    assert decision_dependency_violations(sources) == [
        "Forbidden dependency: acgs.market_data"
    ]


def test_wall_clock_capability_is_detected_by_module_not_class_name() -> None:
    sources = {
        Path("strategy.py"): "from acgs.helper import SystemClock",
        Path("helper.py"): "class SystemClock: pass",
    }
    assert decision_dependency_violations(sources) == []
    sources[Path("helper.py")] = "from acgs import wallclock"
    assert decision_dependency_violations(sources) == [
        "Forbidden dependency: acgs.wallclock"
    ]


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
        "from acgs.clock import *\ndatetime.now()\n",
        "__import__('acgs.market_data')\n",
        "import sys\nsys.modules['acgs.market_data']\n",
        "import importlib\nimportlib.import_module('acgs.market_data')\n",
        "import time\ntime.time()\n",
        "from datetime import UTC, datetime\ndatetime.now(UTC)\n",
        "from datetime import datetime as moment\nmoment.utcnow()\n",
        "import datetime\ndatetime.date.today()\n",
        "from acgs.clock import Clock\n\n\ndef stamp(clock: Clock):\n    return clock.now().now()\n",
        (
            "from acgs.clock import Clock\n\n\ndef stamp(clock: Clock):\n"
            "    instant = clock.now()\n    return instant.now()\n"
        ),
        (
            "from acgs.clock import Clock\n\n\ndef local(clock: Clock):\n"
            "    return clock.now().astimezone()\n"
        ),
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


def test_traversal_stops_at_capability_modules() -> None:
    sources = {
        Path("strategy.py"): "from acgs.__main__ import main\n",
        Path("__main__.py"): "from acgs.smoke import run_smoke\n",
        Path("smoke.py"): "import sqlite3\n",
    }
    assert decision_dependency_violations(sources) == [
        "Forbidden dependency: acgs.__main__"
    ]
