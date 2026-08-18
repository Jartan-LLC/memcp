"""Measure what a local model actually buys you on the cognee backend.

`--llm-base-url` proves a request reaches a local endpoint. It says nothing about
whether the extraction and the embeddings that come back are worth storing, and the
difference matters: cognee's whole reason to exist over the keyless default is that it
retrieves by meaning and builds a graph. If a small local model cannot do either, then
`memcp up --backend cognee --llm-base-url ...` is a slower, more expensive sqlite.

So this measures, on one corpus, three things:

- **recall@1 and recall@3 on paraphrased queries.** Every query is written to share as
  few content words with its memory as possible, so a lexical matcher cannot succeed by
  accident. That is the number that says whether semantic retrieval happened.
- **the same corpus and queries against `sqlite`**, memcp's keyless keyword backend, as
  the comparator. A cognee that does not beat it is not earning the model.
- **the size of the graph** cognee extracted — entities and the relationships between
  them, which is the capability no flat backend has.

Run it against a cognee pointed at whatever endpoint you want to characterise:

    COGNEE_API_BASE=http://127.0.0.1:8011 \\
    COGNEE_TENANT_SECRET=measurement \\
    python tools/local_model_retrieval.py --label "Qwen2.5-1.5B-Instruct Q4_K_M"

It reports a number for one model on one corpus. It is not a benchmark of local models
in general, and a result from it belongs in a document that says which model produced it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from memcp.backend.base import MemoryBackend
from memcp.backend.cognee import CogneeBackend
from memcp.backend.sqlite import SqliteBackend


@dataclass(frozen=True)
class Probe:
    memory: str
    query: str


# Each query asks for the memory in words the memory does not use. "Who keeps an eye on
# the adapters before they ship" has no content word in common with "Ferro reviews
# adapter changes before they reach the release branch" beyond `adapter`, and several
# have none at all. A keyword matcher should do badly here; that is the point.
CORPUS: tuple[Probe, ...] = (
    Probe(
        "The nightly reindex starts at 02:40 UTC and takes roughly eleven minutes",
        "when does the overnight rebuild of the index happen",
    ),
    Probe(
        "Ferro reviews adapter changes before they reach the release branch",
        "who signs off on backend connector work prior to shipping",
    ),
    Probe(
        "The staging bearer token rotates every fourteen days automatically",
        "how often is the pre-production credential replaced",
    ),
    Probe(
        "Latency budget for a single search call is 400 milliseconds at p95",
        "what response time are lookups expected to stay under",
    ),
    Probe(
        "Corin runs the verification package before any release is approved",
        "who checks the build works ahead of a launch",
    ),
    Probe(
        "The pgvector collection for tenant onboarding is named memcp_primary",
        "what is the vector table called for new customer setup",
    ),
    Probe(
        "Jonathan prefers absolute dates over relative ones in every report",
        "how should timestamps be written for the principal",
    ),
    Probe(
        "Thorne must see any change that touches authentication before it merges",
        "who reviews work affecting login and credentials",
    ),
    Probe(
        "The AGPL obligations register lives in the codex repository",
        "where is the licence compliance record kept",
    ),
    Probe(
        "Wren writes the specification that every engineering issue references",
        "who authors the requirements documents",
    ),
    Probe(
        "Rook owns whether the runtime machinery is running at all",
        "who is responsible for infrastructure availability",
    ),
    Probe(
        "The brain deployment serves fifteen agents through one bearer token",
        "how many assistants share the production memory credential",
    ),
)

GRAPH_TEXT = (
    "Ada Lovelace worked with Charles Babbage on the Analytical Engine in London, "
    "and Grace Hopper later built the first compiler at Univac."
)


@dataclass
class Result:
    """What one backend did with the corpus.

    `failures` is not an error path — it is a result. A model too weak to answer
    cognee's extraction schema makes `add_memory` fail outright rather than degrade,
    and a measurement that crashed on the first one would have reported nothing about
    the eleven that followed.
    """

    stored: int = 0
    failures: list[str] = field(default_factory=list)
    at_one: int = 0
    at_three: int = 0


async def measure(backend: MemoryBackend, tenant: str) -> Result:
    result = Result()
    landed: list[Probe] = []
    for probe in CORPUS:
        try:
            await backend.add(tenant, probe.memory, infer=False)
        except Exception as e:  # the failure is the measurement
            result.failures.append(f"{type(e).__name__}: {str(e)[:160]}")
            continue
        result.stored += 1
        landed.append(probe)
    for probe in landed:
        hits = await backend.search(tenant, probe.query, limit=3)
        contents = [m.content for m in hits]
        if contents[:1] == [probe.memory]:
            result.at_one += 1
        if probe.memory in contents:
            result.at_three += 1
    return result


async def graph_size(backend: CogneeBackend, tenant: str) -> tuple[int, int] | str:
    try:
        await backend.add(tenant, GRAPH_TEXT, infer=True)
    except Exception as e:  # the failure is the measurement
        return f"write failed: {type(e).__name__}: {str(e)[:160]}"
    result = await backend.entities(tenant)
    return len(result.entities), len(result.relationships)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="which model produced this result")
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help=(
            "seconds to allow one write. The adapter's own default is 120, which a "
            "small model on CPU exceeds — raising it here is how you measure such an "
            "endpoint rather than time out against it (default: 900)"
        ),
    )
    args = parser.parse_args()

    base = os.environ.get("COGNEE_API_BASE")
    secret = os.environ.get("COGNEE_TENANT_SECRET")
    if not base or not secret:
        print("COGNEE_API_BASE and COGNEE_TENANT_SECRET are required")
        return 2

    total = len(CORPUS)
    tenant = f"measure_{uuid.uuid4().hex[:10]}"

    keyword = SqliteBackend(Path(tempfile.mkdtemp(prefix="memcp-measure-")) / "memcp.sqlite3")
    try:
        keyword_result = await measure(keyword, tenant)
    finally:
        await keyword.close()

    cognee = CogneeBackend(base, secret, timeout=args.timeout)
    try:
        cognee_result = await measure(cognee, tenant)
        graph = await graph_size(cognee, f"{tenant}_graph")
    finally:
        await cognee.close()

    print(f"model under test: {args.label}")
    print(f"corpus: {total} memories, {total} paraphrased queries")
    print()
    print(f"{'backend':<10} {'stored':>8} {'recall@1':>10} {'recall@3':>10}")
    for name, outcome in (("sqlite", keyword_result), ("cognee", cognee_result)):
        print(
            f"{name:<10} {outcome.stored:>5}/{total:<2} "
            f"{outcome.at_one:>6}/{total:<3} {outcome.at_three:>6}/{total:<3}"
        )
    print()
    if isinstance(graph, str):
        print(f"graph from one two-clause sentence: {graph}")
    else:
        print(f"graph from one two-clause sentence: {graph[0]} entities, {graph[1]} relationships")
    for name, outcome in (("sqlite", keyword_result), ("cognee", cognee_result)):
        for failure in outcome.failures:
            print(f"  {name} write failed — {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
