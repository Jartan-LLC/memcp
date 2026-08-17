"""The corpus the round trip moves between backends.

Every entry carries the query that must find it, because A3's assertion is not
"the content is present" but "the same query still retrieves it". Content is
deliberately word-distinctive: the in-memory backend ranks by query-word overlap
and mem0 ranks by embedding similarity, and a distinctive phrase puts the target
in the first page under either.

Three scopes, and two contents that appear in two scopes each — that pair is what
content-only import dedup used to swallow (GitHub #30).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCOPE_A: dict[str, Any] = {"agent_id": "conformance_alpha"}
SCOPE_B: dict[str, Any] = {"agent_id": "conformance_beta"}
SCOPE_C: dict[str, Any] = {"agent_id": "conformance_alpha", "run_id": "conformance_run_7"}


@dataclass(frozen=True)
class CorpusEntry:
    content: str
    query: str
    scope: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


_SHARED_ONE = "Quarterly capacity planning happens in the ledger workbook, never in chat"
_SHARED_TWO = "Escalations older than nine days move to the standing review agenda"


def round_trip_corpus() -> list[CorpusEntry]:
    """24 memories across 3 scopes, including 2 contents duplicated across scopes."""
    return [
        CorpusEntry(
            "The pgvector collection for tenant onboarding is named memcp_primary",
            "pgvector collection tenant onboarding",
            SCOPE_A,
            {"source": "runbook", "confidence": "high"},
        ),
        CorpusEntry(
            "Nightly reindex starts at 02:40 UTC and takes roughly eleven minutes",
            "nightly reindex start time duration",
            SCOPE_A,
            {"source": "runbook"},
        ),
        CorpusEntry(
            "Ferro reviews adapter changes before they reach the release branch",
            "who reviews adapter changes release branch",
            SCOPE_A,
        ),
        CorpusEntry(
            "The staging bearer token rotates every fourteen days automatically",
            "staging bearer token rotation interval",
            SCOPE_A,
            {"sensitivity": "internal"},
        ),
        CorpusEntry(
            "Latency budget for a single search call is 400 milliseconds at p95",
            "latency budget search p95 milliseconds",
            SCOPE_A,
        ),
        CorpusEntry(
            "Import writes verbatim because extraction would rewrite migrated facts",
            "import verbatim extraction rewrite migrated",
            SCOPE_A,
            {"source": "decision"},
        ),
        CorpusEntry(
            "Scope keys accepted by both adapters are agent_id and run_id only",
            "scope keys accepted adapters agent run",
            SCOPE_A,
        ),
        CorpusEntry(
            "A capability a backend claims but cannot serve counts as a failure",
            "capability claimed cannot serve failure",
            SCOPE_A,
        ),
        CorpusEntry(
            _SHARED_ONE,
            "quarterly capacity planning ledger workbook",
            SCOPE_A,
            {"shared_across_scopes": True},
        ),
        CorpusEntry(
            _SHARED_TWO,
            "escalations nine days standing review agenda",
            SCOPE_A,
        ),
        CorpusEntry(
            "The graph adapter lands after the seam has a passing conformance gate",
            "graph adapter lands after conformance gate",
            SCOPE_B,
            {"source": "roadmap"},
        ),
        CorpusEntry(
            "Provisioning must print every container and volume before creating any",
            "provisioning print containers volumes before creating",
            SCOPE_B,
        ),
        CorpusEntry(
            "Fifteen agents read the deployment, so tool argument shapes are frozen",
            "fifteen agents tool argument shapes frozen",
            SCOPE_B,
            {"source": "constraint", "confidence": "high"},
        ),
        CorpusEntry(
            "Benchmark numbers we did not measure are attributed to whoever published them",
            "benchmark numbers attributed whoever published",
            SCOPE_B,
        ),
        CorpusEntry(
            "Vendoring upstream engine source needs a legal read before it lands",
            "vendoring upstream source legal read",
            SCOPE_B,
            {"source": "constraint"},
        ),
        CorpusEntry(
            "Export caps at ten thousand memories and flags the payload as truncated",
            "export cap ten thousand truncated payload",
            SCOPE_B,
        ),
        CorpusEntry(
            "Pagination cursors are plain offsets until opaque tokens land in v0.3",
            "pagination cursors plain offsets opaque tokens",
            SCOPE_B,
        ),
        CorpusEntry(
            _SHARED_ONE,
            "quarterly capacity planning ledger workbook",
            SCOPE_B,
            {"shared_across_scopes": True},
        ),
        CorpusEntry(
            _SHARED_TWO,
            "escalations nine days standing review agenda",
            SCOPE_B,
        ),
        CorpusEntry(
            "Run seven reproduced the undeclared metadata loss on the first attempt",
            "run seven reproduced undeclared metadata loss",
            SCOPE_C,
            {"source": "evidence"},
        ),
        CorpusEntry(
            "The fake embedder hashes tokens into buckets so retrieval stays lexical",
            "fake embedder hashes tokens buckets lexical",
            SCOPE_C,
        ),
        CorpusEntry(
            "Deleting by scope needs at least one scope key or the call is refused",
            "deleting by scope refused without key",
            SCOPE_C,
        ),
        CorpusEntry(
            "History entries are backend-specific strings and are not normalised yet",
            "history entries backend specific not normalised",
            SCOPE_C,
        ),
        CorpusEntry(
            "A round trip that loses ranking still preserves content and scope",
            "round trip loses ranking preserves content scope",
            SCOPE_C,
            {"source": "finding"},
        ),
    ]


def scope_count() -> int:
    return len({tuple(sorted(e.scope.items())) for e in round_trip_corpus()})
