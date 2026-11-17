"""M0 capability boundaries (T-13, T-16), checked by pytest in both native CI jobs."""

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
WALL_CLOCK_CLASSES = {"datetime.date", "datetime.datetime"}
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


def reference_bindings(module: str, tree: ast.AST) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.partition(".")[0]
                origin = alias.name if alias.asname else name
                bindings.setdefault(f"{module}.{name}", set()).add(origin)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                bindings.setdefault(f"{module}.{name}", set()).add(
                    f"{node.module}.{alias.name}"
                )
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            origin = dotted_reference(node.value)
            if origin is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(f"{module}.{target.id}", set()).add(
                        f"{module}.{origin}"
                    )
    return bindings


def is_wall_clock_class(
    name: str, bindings: dict[str, set[str]], followed: frozenset[str] = frozenset()
) -> bool:
    if name in WALL_CLOCK_CLASSES:
        return True
    for prefix in module_and_packages(name):
        if prefix in bindings:
            if prefix in followed:
                return False
            suffix = name[len(prefix) :]
            return any(
                is_wall_clock_class(origin + suffix, bindings, followed | {prefix})
                for origin in bindings[prefix]
            )
    return False


def decision_violations(
    source: str,
    module: str = "acgs.strategy",
    bindings: dict[str, set[str]] | None = None,
) -> list[str]:
    """Apply the same purity checks to decision modules and reachable helpers."""
    tree = ast.parse(source)
    if bindings is None:
        bindings = reference_bindings(module, tree)
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
            target = dotted_reference(node.value)
            if target is not None and is_wall_clock_class(
                f"{module}.{target}", bindings
            ):
                problems.append(f"{target}.{node.attr}")
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
        graph[module] = dependencies

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
    bindings: dict[str, set[str]] = {}
    for module, path in reachable_sources.items():
        bindings.update(reference_bindings(module, ast.parse(sources[path])))
    for module, path in reachable_sources.items():
        violations = decision_violations(sources[path], module, bindings)
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
