# Self-contained cognee for conformance runs

Brings up cognee with no external API key, so the cognee half of the conformance
suite runs on every pull request.

```bash
ci/cognee/up.sh                                # build the stand-in, pull cognee, wait
export COGNEE_API_BASE=http://127.0.0.1:8890
export COGNEE_TENANT_SECRET=memcp-conformance-tenant-secret
python -m memcp.conformance
ci/cognee/down.sh
```

## What runs

| Service | What it is |
| --- | --- |
| `fake-openai` | ~200 lines of standard library serving `/v1/embeddings` and `/v1/chat/completions` |
| `cognee` | The real cognee server, image pinned by digest to the 1.5.0 release |

Cognee carries its own databases — Kuzu for the graph, LanceDB for vectors, SQLite
for everything else — so unlike `ci/mem0` there is no datastore service beside it.

The image is pinned by digest rather than tag, and the pin is kept equal to
`memcp.deploy.images.COGNEE` by a test. Moving it means re-running the conformance
suite: dataset scoping, content-hash dedup and the per-user isolation the adapter's
whole tenant model rests on are behaviours cognee has changed before and documents
nowhere.

## What it proves, and what it does not

Proves: the cognee adapter against a real cognee pipeline — real ingestion, a real
Kuzu graph with real edges behind `memory_entities`, real per-user partitioning with
`ENABLE_BACKEND_ACCESS_CONTROL` on, and the synchronous `remember` path that makes
`add_memory` return only once the memory is findable.

Does not prove:

- **Extraction quality.** The stand-in is a deterministic extractor, not a language
  model: proper-noun phrases become entities and consecutive ones become an edge. The
  graph is genuinely cognee's; which entities are in it is not a model's judgement.
- **Embedding quality.** Token-hash bags again, so similarity is normalised word
  overlap. Retrieval is deterministic and lexical, which is what the round trip needs,
  and is not a semantic ranking claim.
- **That cognee is any good against a small local model.** Pointing it at
  `--llm-base-url` proves a request reaches an endpoint. Nothing here measures whether
  what comes back is worth storing, and nothing in this repository claims it is.

## Credentials

Every value in `docker-compose.yml` is a fixed literal. The stack binds to
`127.0.0.1`, holds only test corpora, and is destroyed with its volumes. None of
these strings is used by any deployment, and none belongs in one.
