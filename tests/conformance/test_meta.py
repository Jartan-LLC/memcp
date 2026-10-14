"""Tests on the conformance suite itself: that no optional method or capability
escapes it, and that the corpus still exercises what the round trip claims."""

from __future__ import annotations

import inspect
from collections import Counter

from memcp.backend.base import MemoryBackend
from memcp.backend.capabilities import ALL_CAPABILITIES, OPTIONAL_CAPABILITIES
from tests.conformance import test_optional, test_required
from tests.conformance.corpus import round_trip_corpus
from tests.conformance.test_round_trip import SEARCH_LIMIT


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


def _optional_methods() -> set[str]:
    """Public concrete methods on MemoryBackend, other than close()."""
    abstract = set(MemoryBackend.__abstractmethods__)
    return {
        name
        for name, obj in vars(MemoryBackend).items()
        if not name.startswith("_")
        and name not in abstract
        and name != "close"
        and inspect.isfunction(obj)
    }


def test_every_optional_method_has_a_capability():
    """A new optional method on MemoryBackend must be given a capability name."""
    mapped = set(OPTIONAL_CAPABILITIES.values())
    assert _optional_methods() == mapped, (
        f"MemoryBackend's optional methods {sorted(_optional_methods())} do not match the "
        f"capability map {sorted(mapped)}. Add the method to OPTIONAL_CAPABILITIES in "
        "memcp/backend/capabilities.py and write its conformance tests."
    )


def test_every_capability_is_exercised():
    """Each capability needs at least one test that declares it."""
    missing = set(ALL_CAPABILITIES) - _conformance_markers(test_optional)
    assert not missing, (
        f"no conformance test declares {sorted(missing)}, so every backend would skip it."
    )


def test_required_suite_has_no_capability_markers():
    """Nothing in the required suite may be capability-gated, or it could skip."""
    assert not _conformance_markers(test_required)


def test_corpus_spans_several_scopes_with_a_query_each():
    corpus = round_trip_corpus()
    assert len(corpus) >= 20
    assert len({tuple(sorted(e.scope.items())) for e in corpus}) >= 2
    assert all(e.query and e.content for e in corpus)


def test_search_limit_is_below_every_scope_size():
    """Otherwise a search that ignores the query returns everything and passes."""
    sizes = Counter(tuple(sorted(e.scope.items())) for e in round_trip_corpus())
    assert min(sizes.values()) > SEARCH_LIMIT


def test_corpus_repeats_content_across_scopes():
    """Without this, the round trip stops exercising scope-aware import dedup."""
    counts = Counter(e.content for e in round_trip_corpus())
    repeated = [content for content, n in counts.items() if n > 1]
    assert repeated, "the corpus must hold content that appears in more than one scope"
    for content in repeated:
        scopes = {
            tuple(sorted(e.scope.items())) for e in round_trip_corpus() if e.content == content
        }
        assert len(scopes) > 1, f"{content!r} is repeated within one scope, not across scopes"
