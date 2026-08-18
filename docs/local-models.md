# memcp against a local model

`--llm-base-url` points the `mem0` and `cognee` backends at any OpenAI-compatible
endpoint, which takes the provider account out of the stack. That option proves a
request reaches the endpoint. It proves nothing about what comes back, and the two
are easy to confuse: a stack that comes up healthy and stores a memory can still be
worse than the keyless default it replaced.

This document holds measurements, each naming the model that produced it. There is
no general claim here about local models, and there is not meant to be.

## Method

`tools/local_model_retrieval.py`. Twelve memories, and twelve queries written to
share as few content words with their memory as possible — "how often is the
pre-production credential replaced" for "The staging bearer token rotates every
fourteen days automatically". A lexical matcher cannot answer those by accident, so
recall on them reads as whether semantic retrieval happened at all.

The same corpus runs through `sqlite`, memcp's keyless keyword backend, as the
comparator. That is the bar: cognee costs a model call per write and seconds to
minutes of latency, and if it does not beat word overlap on paraphrased queries it is
not earning them.

A write that fails is recorded rather than raised, because it can fail: cognee asks
the model for a schema-shaped knowledge graph, and a model that cannot produce one
makes `add_memory` fail outright instead of degrading.

## Qwen2.5-1.5B-Instruct Q4_K_M, CPU only

Measured 2026-08-17 on this workspace's runtime: llama.cpp (`llama-cpp-python`
0.3.35) serving `Qwen2.5-1.5B-Instruct` Q4_K_M for extraction and
`nomic-embed-text-v1.5` Q4_K_M for embeddings, five CPU cores, no GPU, against
cognee 1.5.0.

**The recall table is not here, because the run did not finish.** Two runs were
killed by unrelated infrastructure outages after 60–90 minutes, and a third by the
same. What follows is what those runs did establish, which is enough to answer the
question this document exists for.

- **A single `add_memory` takes minutes, not seconds.** One write drives roughly six
  to seven completions through cognee's pipeline — classification, chunk extraction,
  graph extraction, summarisation, and instructor's retries when the model's JSON does
  not validate. At the 30–45 seconds per completion this hardware managed, that is
  three to five minutes per memory.
- **It exceeds memcp's own default timeout.** The adapter allowed 120 seconds per
  call; the first run failed with `MemoryAPIError(408)` on a write. `COGNEE_TIMEOUT`
  exists because of this measurement — the default is comfortable against a hosted
  model and wrong against this one.
- **Some writes fail outright.** The second run returned `409` from cognee with
  `1 validation error for KnowledgeGraph: nodes.0.description Field required` after
  instructor exhausted its retries. The model could not produce cognee's extraction
  schema, and `add_memory` failed rather than storing the memory without a graph.
  That is the failure mode worth knowing about: not a worse graph, no memory.

**What this does not say.** It does not say cognee retrieves badly against this
model — the recall numbers were never produced. It does not generalise to a larger
local model, to a GPU, or to a served runtime with continuous batching. Anyone with
either can produce the missing table:

```bash
COGNEE_API_BASE=http://127.0.0.1:8000 \
COGNEE_TENANT_SECRET=measurement \
python tools/local_model_retrieval.py --label "<model>" --timeout 900
```

**What it does say**, and what the rest of the documentation is held to: on hardware
of this shape, `memcp up --backend cognee --llm-base-url ...` is not a working
company brain. Writes take minutes and some of them fail. No README, changelog, tool
description or deployment note in this repository claims otherwise, and until a run
of the above says something better, none should.
