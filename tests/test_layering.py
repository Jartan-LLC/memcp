"""The layering rules in docs/architecture.md, checked against the code.

Imports go down the package table (http, tools, policy, backends), stay inside one
package, or reach the shared modules. Only the app and the tool-surface generator
construct a backend. Only `memcp.policy` calls a backend method, and `tools/` and
`http/` never touch `._backend`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

import memcp
from memcp.backends.capabilities import ALL_CAPABILITIES
from memcp.backends.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.http import app as http_app
from memcp.policy import Policy
from memcp.tools import register_tools
from memcp.types import EntitiesResult

PACKAGE_ROOT = Path(memcp.__file__).parent
LAYERS = ("http", "tools", "policy", "backends")
SHARED = {"types", "identity", "config"}


def _module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported_modules(path: Path) -> set[str]:
    """Every memcp module `path` imports, as a dotted name."""
    package = _module_name(path) if path.name == "__init__.py" else _module_name(path.parent)
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            found.add(module)
            # `from memcp import x` names a module when x is one.
            found.update(f"{module}.{a.name}" for a in node.names if module == "memcp")
    return {m for m in found if m == "memcp" or m.startswith("memcp.")}


def _top(module: str) -> str:
    """The top-level part of memcp a module belongs to: a package, a shared module, or root."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _allowed(importer: str, imported: str) -> bool:
    source, target = _top(importer), _top(imported)
    if target not in (*LAYERS, *SHARED, "__main__"):
        return True  # root `memcp` or a name imported from it, such as __version__
    if source == "__main__":
        return True
    if source in SHARED:
        return target in SHARED
    if target in SHARED or target == source:
        return True
    return target in LAYERS and LAYERS.index(target) > LAYERS.index(source)


def test_imports_follow_the_package_table():
    violations = [
        f"{_module_name(path)} imports {imported}"
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        for imported in sorted(_imported_modules(path))
        if not _allowed(_module_name(path), imported)
    ]
    assert violations == []


def test_the_import_reader_finds_absolute_and_relative_imports():
    policy_imports = _imported_modules(PACKAGE_ROOT / "policy" / "__init__.py")
    assert {"memcp.policy.scope", "memcp.backends", "memcp.types"} <= policy_imports
    in_memory_imports = _imported_modules(PACKAGE_ROOT / "backends" / "in_memory.py")
    assert {"memcp.backends.base", "memcp.backends.keyword"} <= in_memory_imports


def test_tools_and_http_never_reach_into_the_policy_object():
    reaching = [
        f"{_module_name(path)}:{node.lineno}"
        for package in ("tools", "http")
        for path in sorted((PACKAGE_ROOT / package).rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute) and node.attr == "_backend"
    ]
    assert reaching == []


def test_the_import_check_catches_an_upward_import():
    assert not _allowed("memcp.policy.scope", "memcp.tools._shared")
    assert not _allowed("memcp.backends.sqlite", "memcp.policy.transfer")
    assert not _allowed("memcp.types", "memcp.backends.base")
    assert _allowed("memcp.tools.memories", "memcp.policy")
    assert _allowed("memcp.backends.mem0.adapter", "memcp.backends.base")


BACKEND_BUILDERS = {"memcp.http.app", "memcp.tools.surface"}


def _backend_classes(trees: list[ast.Module]) -> set[str]:
    """Names of every class in memcp that subclasses MemoryBackend, however indirectly."""
    classes = [n for tree in trees for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    found = {"MemoryBackend"}
    while True:
        more = {
            c.name
            for c in classes
            if any((getattr(b, "id", None) or getattr(b, "attr", None)) in found for b in c.bases)
            and c.name not in found
        }
        if not more:
            return found - {"MemoryBackend"}
        found |= more


def _constructs(tree: ast.Module, classes: set[str]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) in classes
        for node in ast.walk(tree)
    )


def test_only_the_app_and_the_generator_construct_a_backend():
    trees = {_module_name(p): ast.parse(p.read_text()) for p in PACKAGE_ROOT.rglob("*.py")}
    classes = _backend_classes(list(trees.values()))
    builders = {
        module
        for module, tree in trees.items()
        if not module.startswith("memcp.backends") and _constructs(tree, classes)
    }
    assert builders == BACKEND_BUILDERS


class _EveryCapability(InMemoryBackend):
    def capabilities(self) -> set[str]:
        return set(ALL_CAPABILITIES)

    async def entities(
        self, partition: str, *, scope: dict[str, Any] | None = None, limit: int = 100
    ) -> EntitiesResult:
        return EntitiesResult()


class _CallerRecorder:
    """Wraps a backend and records, for each method call, the module that made it."""

    def __init__(self, inner: InMemoryBackend) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            caller = sys._getframe(1).f_globals["__name__"]
            self.calls.append((name, caller))
            return attr(*args, **kwargs)

        return call


class _Tools:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def tool(self, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.handlers[fn.__name__] = fn
            return fn

        return decorator


async def test_only_policy_calls_a_backend(config: Config, monkeypatch: Any):
    recorder = _CallerRecorder(_EveryCapability())

    tools = _Tools()
    # The recorder stands in for a MemoryBackend; Policy only calls its methods.
    register_tools(tools, Policy(recorder), config)  # type: ignore[arg-type]
    call = tools.handlers
    added = await call["add_memory"](content="layering check", infer=False)
    memory_id = added["results"][0]["id"] if "results" in added else added["id"]
    arguments: dict[str, dict[str, Any]] = {
        "search_memory": {"query": "layering"},
        "get_memory": {"memory_id": memory_id},
        "update_memory": {"memory_id": memory_id, "content": "layering check, updated"},
        "memory_history": {"memory_id": memory_id},
        "list_memories": {},
        "memory_entities": {},
        "memory_status": {},
        "export_memories": {},
        "import_memories": {"memories": [{"content": "imported"}]},
        "delete_memory": {"memory_id": memory_id},
        "delete_all_memories": {"scope": {"agent_id": "nobody"}},
    }
    assert {"add_memory", *arguments} == set(call), "every registered tool runs here"
    results = [added] + [await call[name](**kwargs) for name, kwargs in arguments.items()]
    refused = [r for r in results if isinstance(r, dict) and "error" in r]
    assert refused == [], (
        "every call must succeed, or it never reaches the backend and proves nothing"
    )

    monkeypatch.setattr(http_app, "_create_backend", lambda _config: recorder)
    app, _ = http_app.create_app(config)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        await client.get("/health")

    called = {name for name, _ in recorder.calls}
    assert {"add", "search", "get", "update", "delete", "delete_all", "list_memories"} <= called
    assert {"history", "entities", "health", "close", "capabilities", "scope_keys"} <= called
    outside = sorted({(n, c) for n, c in recorder.calls if not c.startswith("memcp.policy")})
    assert outside == []
