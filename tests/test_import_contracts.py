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
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "email",
    "enum",
    "functools",
    "hashlib",
    "itertools",
    "io",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "sqlite3",
    "sys",
    "tempfile",
    "time",
    "tomllib",
    "types",
    "typing",
    "zlib",
    "zipfile",
}
PUBLIC_HTTP_IMPORTS = {"http.client", "urllib.error", "urllib.request"}
DECISION_LAYERS = {
    "strategy", "features", "universe", "research", "feasibility", "eligibility", "broker_sim",
}
CAPABILITY_MODULES = {
    "acgs.market_data",
    "acgs.capture",
    "acgs.config_io",
    "acgs.execution",
    "acgs.ledger",
    "acgs.history_io",
    "acgs.research_io",
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
    "types",
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


SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def clock_spellings(tree: ast.AST) -> set[str]:
    """Annotation spellings that resolve to `acgs.clock.Clock`, including aliases."""
    spellings = {"acgs.clock.Clock"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == "acgs.clock" and alias.name == "Clock":
                    spellings.add(bound)
                elif node.module == "acgs" and alias.name == "clock":
                    spellings.add(f"{bound}.Clock")
    return spellings


def in_scope(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """Nodes of one scope; nested functions and classes are yielded but not entered."""
    pending = list(nodes)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, SCOPE_NODES):
            pending.extend(ast.iter_child_nodes(node))


def parameters(arguments: ast.arguments) -> list[ast.arg]:
    extra = [arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None]
    return [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, *extra]


def wall_clock_violations(tree: ast.Module) -> list[str]:
    """`now`/`utcnow`/`today` read the wall clock on any datetime class or instance.

    Only a name that holds the injected `Clock` in the current scope (a parameter or
    variable annotated as `acgs.clock.Clock`, under any import alias) or a `self`
    field annotated or assigned from such a parameter may provide the time.
    """
    spellings = clock_spellings(tree)
    problems: list[str] = []

    def is_clock(annotation: ast.expr | None) -> bool:
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return False
        return annotation is not None and dotted_reference(annotation) in spellings

    def scope_clocks(
        body: list[ast.AST], inherited: frozenset[str], params: list[ast.arg]
    ) -> frozenset[str]:
        bound = {param.arg for param in params}
        clocks = {param.arg for param in params if is_clock(param.annotation)}
        annotated_targets = set()
        rebound = set()
        for node in in_scope(body):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if is_clock(node.annotation):
                    clocks.add(node.target.id)
                    annotated_targets.add(id(node.target))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bound.add(node.name)
            elif isinstance(node, ast.alias):
                bound.add((node.asname or node.name).partition(".")[0])
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store)
                and id(node) not in annotated_targets
            ):
                bound.add(node.id)
                rebound.add(node.id)
        return frozenset((inherited - bound) | (clocks - rebound))

    def class_fields(node: ast.ClassDef) -> frozenset[str]:
        fields = set()
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and is_clock(statement.annotation)
            ):
                fields.add(statement.target.id)
            elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                params = parameters(statement.args)
                if not params:
                    continue
                owner = params[0].arg
                clock_params = {p.arg for p in params if is_clock(p.annotation)}
                for inner in in_scope(statement.body):
                    if isinstance(inner, ast.AnnAssign) and is_clock(inner.annotation):
                        targets = [inner.target]
                    elif (
                        isinstance(inner, ast.Assign)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id in clock_params
                    ):
                        targets = inner.targets
                    else:
                        continue
                    fields.update(
                        target.attr
                        for target in targets
                        if isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == owner
                    )
        return frozenset(fields)

    def visit_function(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        clocks: frozenset[str],
        fields: frozenset[str],
        owner: str | None,
    ) -> None:
        arguments = node.args
        outer = [*arguments.defaults, *(d for d in arguments.kw_defaults if d is not None)]
        if not isinstance(node, ast.Lambda):
            outer.extend(node.decorator_list)
        for expression in outer:
            visit(expression, clocks, fields, owner)
        body = node.body if isinstance(node.body, list) else [node.body]
        inner = scope_clocks(body, clocks, parameters(arguments))
        for child in body:
            visit(child, inner, fields, owner)

    def visit(
        node: ast.AST, clocks: frozenset[str], fields: frozenset[str], owner: str | None
    ) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            visit_function(node, clocks, fields, owner)
            return
        if isinstance(node, ast.ClassDef):
            for expression in [*node.decorator_list, *node.bases]:
                visit(expression, clocks, fields, owner)
            own_fields = class_fields(node)
            for statement in node.body:
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    params = parameters(statement.args)
                    method_owner = params[0].arg if params else None
                    visit_function(statement, clocks, own_fields, method_owner)
                else:
                    visit(statement, clocks, frozenset(), None)
            return
        if isinstance(node, ast.Attribute) and node.attr in WALL_CLOCK_CALLS:
            receiver = node.value
            injected = (isinstance(receiver, ast.Name) and receiver.id in clocks) or (
                owner is not None
                and isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == owner
                and receiver.attr in fields
            )
            if not injected:
                problems.append(f"{dotted_reference(receiver) or '<expression>'}.{node.attr}")
        for child in ast.iter_child_nodes(node):
            visit(child, clocks, fields, owner)

    module_clocks = scope_clocks(list(tree.body), frozenset(), [])
    for statement in tree.body:
        visit(statement, module_clocks, frozenset(), None)
    return problems


def decision_violations(source: str) -> list[str]:
    """Apply the same purity checks to decision modules and reachable helpers."""
    tree = ast.parse(source)
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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "astimezone"
            and not node.args
            and not node.keywords
        ):
            problems.append("astimezone() without an explicit time zone")
    problems.extend(wall_clock_violations(tree))
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


def test_clock_exemption_follows_function_scope() -> None:
    source = (
        "from datetime import datetime\n\n"
        "from acgs.clock import Clock\n\n\n"
        "def stamp(clock: Clock) -> datetime:\n"
        "    return clock.now()\n\n\n"
        "def wall(clock: type[datetime]) -> datetime:\n"
        "    return clock.now()\n"
    )
    assert decision_violations(source) == ["clock.now"]


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\nfrom acgs.clock import Clock\n"
        "def f(clock: Clock):\n    clock = datetime\n    return clock.now()\n",
        "from acgs.clock import Clock\n"
        "def f(clock: Clock):\n    return (lambda clock: clock.now())(None)\n",
        "from datetime import datetime\nfrom acgs.clock import Clock\nclock: Clock\n"
        "def f():\n    clock = datetime\n    return clock.now()\n",
        "from acgs.clock import Clock\nclass Job:\n    clock: Clock\n"
        "    def run(self):\n        return self.other.now()\n",
    ],
)
def test_rebound_or_unrelated_names_are_not_exempt(source: str) -> None:
    assert decision_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from acgs.clock import Clock as ReplayClock\n"
        "def f(clock: ReplayClock):\n    return clock.now()\n",
        "from acgs.clock import Clock as ReplayClock\n"
        "def f(clock: 'ReplayClock'):\n    return clock.now()\n",
        "from acgs import clock as clocks\n"
        "def f(source: clocks.Clock):\n    return source.now()\n",
        "from acgs.clock import Clock\n"
        "def outer(clock: Clock):\n    def inner():\n        return clock.now()\n"
        "    return inner\n",
        "from dataclasses import dataclass\nfrom acgs.clock import Clock\n"
        "@dataclass\nclass Job:\n    clock: Clock\n"
        "    def run(self):\n        return self.clock.now()\n",
        "from acgs.clock import Clock\nclass Job:\n"
        "    def __init__(self, clock: Clock):\n        self.clock = clock\n"
        "    def run(self):\n        return self.clock.now()\n",
    ],
)
def test_injected_clock_aliases_and_fields_are_allowed(source: str) -> None:
    assert decision_violations(source) == []
