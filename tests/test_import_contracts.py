"""M0 capability boundaries checked by pytest in both native CI jobs."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "acgs"
SAFE_IMPORTS = {
    "argparse",
    "collections",
    "dataclasses",
    "datetime",
    "email",
    "enum",
    "hashlib",
    "json",
    "pathlib",
    "re",
    "sqlite3",
    "sys",
    "time",
    "tomllib",
    "typing",
    "contextlib",
}
PUBLIC_HTTP_IMPORTS = {"http.client", "urllib.error", "urllib.request"}
DECISION_LAYERS = {"strategy", "features", "universe", "research"}
CAPABILITY_LAYERS = {"market_data", "capture", "execution", "transfers", "__main__"}


def imported_modules(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                "Use absolute imports for auditable capability boundaries"
            )
            assert node.module is not None
            if node.module == "acgs":
                names.update(f"acgs.{alias.name}" for alias in node.names)
            else:
                names.add(node.module)
    return names


def test_m0_has_only_public_http_and_no_order_or_transfer_adapter() -> None:
    for path in SOURCE.rglob("*.py"):
        parts = path.relative_to(SOURCE).with_suffix("").parts
        assert {"execution", "transfers"}.isdisjoint(parts)
        for name in imported_modules(path.read_text(encoding="utf-8")):
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


def test_strategy_and_research_cannot_import_capabilities_transitively() -> None:
    graph: dict[str, set[str]] = {}
    for path in SOURCE.rglob("*.py"):
        layer = path.relative_to(SOURCE).with_suffix("").parts[0]
        imports = imported_modules(path.read_text(encoding="utf-8"))
        graph.setdefault(layer, set()).update(
            name.split(".")[1] for name in imports if name.startswith("acgs.")
        )
    for layer in DECISION_LAYERS:
        assert CAPABILITY_LAYERS.isdisjoint(reachable_layers(layer, graph))


def test_import_contract_detects_indirect_capability_access() -> None:
    graph = {"strategy": {"helper"}, "helper": {"market_data"}}
    assert CAPABILITY_LAYERS & reachable_layers("strategy", graph) == {"market_data"}
