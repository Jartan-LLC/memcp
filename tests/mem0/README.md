# Self-contained mem0 for conformance runs

Brings up mem0 with no external API key, so the mem0 half of the conformance suite
runs on every pull request (GitHub #28).

```bash
tests/mem0/up.sh  # clone, build, wait for health
export MEM0_API_BASE=http://127.0.0.1:8888
export MEM0_API_KEY=memcp-conformance-admin-key
pytest tests/conformance
tests/mem0/down.sh
```

## What runs

| Service | What it is |
| --- | --- |
| `postgres` | `pgvector/pgvector:pg17` — mem0's real vector store and app database |
| `fake-openai` | A small standard-library stand-in for OpenAI's `/v1/embeddings` and `/v1/chat/completions` |
| `mem0` | mem0's REST server, built from `mem0ai/mem0` at the SHA in `mem0.pin`, with the `mem0ai` package pinned to the version that commit's `pyproject.toml` declares |

mem0 is pinned rather than tracked, so an upstream push can't turn this repository's
`main` red without a commit here.

## Moving the pin

1. Get the release's commit:

   ```bash
   git ls-remote https://github.com/mem0ai/mem0.git refs/tags/vX.Y.Z
   ```

2. Put that SHA in `mem0.pin`.
3. Run the commands at the top of this file; the conformance suite says whether the
   release still honours the adapter's contract.

## What it proves, and what it does not

Proves: the mem0 adapter against real mem0 request handling, real pgvector storage,
real tenant filtering, and mem0's authenticated path — `X-API-Key` is on, not
disabled.

Does not prove:

- **Embedding quality.** `fake-openai` returns token-hash bags, so similarity is
  normalised word overlap. Retrieval is deterministic and lexical, which is what the
  round trip needs, and is not a semantic ranking claim.
- **Fact extraction.** The stand-in answers every completion with "extracted
  nothing", a documented outcome of `add(infer=True)`. Extraction is mem0's LLM
  behaviour, not the seam's, and testing it needs a real provider key.

## Credentials

Every value in `docker-compose.yml` is a fixed literal. The stack binds to
`127.0.0.1`, holds only test corpora, and is destroyed with its volumes. None of
these strings is used by any deployment, and none belongs in one.
