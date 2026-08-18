"""Tests on the conformance suite itself.

The suite proves things about backends. These prove things about the suite: that no
optional method escapes it, that no capability goes uncovered, and that the two
generated documents match their generators. Without them the gates are only as good
as whoever remembers to extend them.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from memcp import toolsurface
from memcp.conformance import capabilities, portability, registry
from memcp.conformance.corpus import round_trip_corpus
from memcp.conformance.suite import test_optional, test_required

REPO_ROOT = Path(__file__).resolve().parents[1]


def _conformance_markers(module: object) -> set[str]:
    found: set[str] = set()
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        for mark in getattr(fn, "pytestmark", []):
            if mark.name == "conformance" and mark.args:
                found.add(str(mark.args[0]))
    return found


# ---------------------------------------------------------------------------
# A2 — no optional method escapes the suite
# ---------------------------------------------------------------------------


def test_every_optional_method_has_a_capability():
    """A new optional method on MemoryBackend must be given a capability name."""
    discovered = capabilities.discover_optional_methods()
    mapped = set(capabilities.OPTIONAL_CAPABILITIES.values())
    assert discovered == mapped, (
        f"MemoryBackend's optional methods {sorted(discovered)} do not match the "
        f"capability map {sorted(mapped)}. Add the method to OPTIONAL_CAPABILITIES in "
        "memcp/conformance/capabilities.py and write its conformance tests."
    )


def test_every_capability_is_exercised():
    """Each capability needs at least one test that declares it."""
    covered = _conformance_markers(test_optional)
    missing = set(capabilities.ALL_CAPABILITIES) - covered
    assert not missing, (
        f"no conformance test declares {sorted(missing)}. A capability with no test "
        "would report SKIP for every backend, which reads as 'not implemented'."
    )


def test_required_methods_come_from_the_abc():
    from memcp.backend.base import MemoryBackend

    assert set(capabilities.REQUIRED_METHODS) == set(MemoryBackend.__abstractmethods__)


def test_required_suite_has_no_capability_markers():
    """Nothing in the required suite may be capability-gated, or it could skip."""
    assert not _conformance_markers(test_required)


# ---------------------------------------------------------------------------
# A4 — the document and the enforced set are the same thing
# ---------------------------------------------------------------------------


def test_portability_doc_matches_the_matrix():
    path = REPO_ROOT / portability.DOC_PATH
    assert path.exists(), f"{portability.DOC_PATH} is missing"
    assert path.read_text(encoding="utf-8") == portability.render_markdown() + "\n", (
        f"{portability.DOC_PATH} is out of date. Regenerate it with `{portability.GENERATOR}`."
    )


def test_every_builtin_pair_is_documented():
    names = [spec.name for spec in registry.builtin_specs()]
    for source in names:
        for target in names:
            assert portability.pair_losses(source, target), (
                f"{source} -> {target} has no portability record"
            )


def test_content_and_scope_cannot_be_declared_lost():
    for aspect in portability.INVIOLABLE:
        with pytest.raises(ValueError):
            portability.Loss(aspect, "should be impossible")


def test_undeclared_reports_only_new_losses():
    declared = portability.declared_aspects("in_memory", "mem0")
    assert portability.undeclared("in_memory", "mem0", set(declared)) == frozenset()
    assert portability.undeclared("in_memory", "mem0", {"metadata"}) == frozenset({"metadata"})


def test_unknown_pair_is_a_loud_failure():
    with pytest.raises(portability.UndocumentedPairError):
        portability.declared_aspects("in_memory", "cognee")


def test_corpus_covers_the_criterion():
    """A3's floor: at least 20 memories across at least 2 scopes, each with a query."""
    corpus = round_trip_corpus()
    assert len(corpus) >= 20
    assert len({tuple(sorted(e.scope.items())) for e in corpus}) >= 2
    assert all(e.query and e.content for e in corpus)


def test_corpus_repeats_content_across_scopes():
    """Without this, the round trip stops exercising scope-aware import dedup."""
    counts = Counter(e.content for e in round_trip_corpus())
    repeated = [content for content, n in counts.items() if n > 1]
    assert repeated, (
        "the corpus must hold content that appears in more than one scope — that is "
        "what a content-only dedup index collapses (GitHub #30)"
    )
    for content in repeated:
        scopes = {
            tuple(sorted(e.scope.items())) for e in round_trip_corpus() if e.content == content
        }
        assert len(scopes) > 1, f"{content!r} is repeated within one scope, not across scopes"


# ---------------------------------------------------------------------------
# A6 — the tool contract is frozen
# ---------------------------------------------------------------------------


def test_tool_surface_matches_the_snapshot():
    path = REPO_ROOT / toolsurface.SNAPSHOT_PATH
    assert path.exists(), f"{toolsurface.SNAPSHOT_PATH} is missing"
    committed = json.loads(path.read_text(encoding="utf-8"))["tools"]
    current = toolsurface.current_surface()

    added = sorted(set(current) - set(committed))
    removed = sorted(set(committed) - set(current))
    assert not removed, (
        f"tool(s) {removed} disappeared from the MCP surface. Fifteen agents read the "
        "live deployment through these names; a removal is a breaking change that "
        f"needs listing on the issue before `{toolsurface.GENERATOR}` is run."
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
    # The one with a knowledge graph, and what brain-mcp.jartan.dev runs. All twelve.
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


def test_nothing_breaks_for_the_live_deployment():
    """brain-mcp.jartan.dev runs mem0, so the twelve tools stay registered there.

    The project rule is that a tool-surface change answers the compatibility question
    rather than assuming it. sqlite and in_memory dropping memory_entities is a change
    to what a keyless install exposes and no change at all to what the fifteen agents
    read.
    """
    assert _registered_tools("mem0") == set(toolsurface.load())
    assert len(_registered_tools("mem0")) == 12
