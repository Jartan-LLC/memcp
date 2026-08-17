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
