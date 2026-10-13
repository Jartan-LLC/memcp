"""The MCP tool surface: the snapshot in docs/tool-surface.json, and what each backend
registers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memcp import toolsurface

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tool_surface_matches_the_snapshot():
    path = REPO_ROOT / toolsurface.SNAPSHOT_PATH
    assert path.exists(), f"{toolsurface.SNAPSHOT_PATH} is missing"
    committed = json.loads(path.read_text(encoding="utf-8"))["tools"]
    current = toolsurface.current_surface()

    added = sorted(set(current) - set(committed))
    removed = sorted(set(committed) - set(current))
    assert not removed, (
        f"tool(s) {removed} disappeared from the MCP surface. Connected clients call "
        "these names, so a removal is a breaking change: state it before running "
        f"`{toolsurface.GENERATOR}`."
    )
    assert not added, (
        f"new tool(s) {added} appeared. Additive is safe for existing clients, but the "
        f"snapshot has to be regenerated deliberately: `{toolsurface.GENERATOR}`."
    )

    changed = {
        name: {"committed": committed[name], "current": current[name]}
        for name in sorted(current)
        if committed[name] != current[name]
    }
    assert not changed, (
        "the argument shape or annotations of "
        f"{sorted(changed)} changed. State what it breaks for a connected client, then "
        f"regenerate with `{toolsurface.GENERATOR}`. Detail: "
        f"{json.dumps(changed, indent=2, sort_keys=True)}"
    )


def test_tool_surface_has_twelve_tools():
    assert len(toolsurface.current_surface()) == 12


# ---------------------------------------------------------------------------
# What each backend actually registers
# ---------------------------------------------------------------------------
#
# docs/tool-surface.json is generated from ALL_CAPABILITIES, so it freezes the full
# surface any backend can expose and no longer moves when one backend declares fewer.
# That is deliberate — an honest undeclaration should not read as the contract
# shrinking — but it means the snapshot stopped being the thing that notices a
# backend losing a tool. This table is what notices instead: it is per backend, it is
# exact, and a capability quietly disappearing fails here with the backend named.

UNIVERSAL_TOOLS = {
    "add_memory",
    "search_memory",
    "delete_memory",
    "delete_all_memories",
    "memory_status",
}

# Every tool each in-repository backend registers. Exact, not a subset: a tool
# appearing is as much a change as one vanishing.
EXPECTED_TOOLS: dict[str, set[str]] = {
    "in_memory": UNIVERSAL_TOOLS
    | {
        "get_memory",
        "update_memory",
        "list_memories",
        "memory_history",
        "export_memories",
        "import_memories",
    },
    "sqlite": UNIVERSAL_TOOLS
    | {
        "get_memory",
        "update_memory",
        "list_memories",
        "memory_history",
        "export_memories",
        "import_memories",
    },
    # The one with a knowledge graph: all twelve.
    "mem0": UNIVERSAL_TOOLS
    | {
        "get_memory",
        "update_memory",
        "list_memories",
        "memory_history",
        "memory_entities",
        "export_memories",
        "import_memories",
    },
}


def _registered_tools(backend_name: str) -> set[str]:
    from memcp.backend.in_memory import InMemoryBackend
    from memcp.backend.mem0 import Mem0Backend
    from memcp.backend.sqlite import SqliteBackend
    from memcp.config import Config
    from memcp.tools import register_tools

    class _Collector:
        def __init__(self) -> None:
            self.names: set[str] = set()

        def tool(self, **_kwargs: object):
            def decorator(fn):
                self.names.add(fn.__name__)
                return fn

            return decorator

    builders = {
        "in_memory": InMemoryBackend,
        "sqlite": lambda: SqliteBackend(":memory:"),
        # Constructed, never called: register_tools only reads capabilities().
        "mem0": lambda: Mem0Backend("http://mem0.invalid", "not-a-real-key"),
    }
    collector = _Collector()
    config = Config.model_validate({"MEMCP_BACKEND": "in_memory", "MEMCP_HOST": "127.0.0.1"})
    register_tools(collector, builders[backend_name](), config)
    return collector.names


@pytest.mark.parametrize("backend_name", sorted(EXPECTED_TOOLS))
def test_backend_registers_exactly_the_tools_it_should(backend_name: str):
    assert _registered_tools(backend_name) == EXPECTED_TOOLS[backend_name]


def test_the_snapshot_is_the_union_of_what_the_backends_register():
    """The frozen contract must not drift away from any real backend's reality.

    Generating it from ALL_CAPABILITIES is only safe while some backend actually
    reaches every tool in it. A capability nothing implements would otherwise sit in
    the snapshot forever as a tool no deployment has ever exposed.
    """
    union: set[str] = set()
    for name in EXPECTED_TOOLS:
        union |= _registered_tools(name)
    assert union == set(toolsurface.load())
