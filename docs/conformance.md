# Backend conformance

One command runs the suite against every backend it can reach and prints what each
one implements:

```bash
python -m memcp.conformance
```

It takes any pytest argument. `--conformance-report=report.json` writes the same
result as JSON.

## The report

```
backend    capability          status  detail
---------  ------------------  ------  -----------------------------
in_memory  (required methods)  PASS    16 passed
in_memory  get_memory          PASS    4 passed
in_memory  list_memories       PASS    4 passed
in_memory  memory_entities     PASS    3 passed
in_memory  memory_history      PASS    4 passed
in_memory  update_memory       PASS    3 passed
in_memory  round_trip          PASS    1 passed
```

`SKIP` means the backend does not declare that capability. It never means "we did not
get round to testing it": a backend that declares a capability and fails its tests
reports `FAIL`.

The suite is self-sufficient: it carries its own asyncio configuration, so it behaves
the same under your pytest settings as under this repository's. `pytest --pyargs
memcp.conformance.suite` is the same run with pytest arguments of your choosing.

## Choosing backends

| Variable | Effect |
| --- | --- |
| `MEMCP_CONFORMANCE_BACKENDS` | Comma-separated names to run. Default: everything available. |
| `MEMCP_CONFORMANCE_EXTRA` | Register out-of-tree adapters: `name=package.module:factory`, comma-separated. |
| `MEM0_API_BASE`, `MEM0_API_KEY` | Required for the `mem0` backend. Without them it reports unavailable. |

`mem0` needs a running server. `ci/mem0/up.sh` stands a disposable one up with no API
key; see `ci/mem0/README.md`.

## Testing your own adapter

The suite ships inside the installed package, so an adapter in its own repository
needs memcp as a dependency and nothing else. Write the backend and declare its
portability pairs in the same module:

```python
# my_adapter/backend.py
from memcp.backend.base import MemoryBackend
from memcp.conformance.portability import IDENTITY_LOSSES, declare_pair


class MyBackend(MemoryBackend):
    ...


# Registered at import time, which is before the suite collects. Every pair you want
# the round trip to run needs one — including MyBackend to itself.
for _pair in (("mystore", "mystore"), ("mystore", "in_memory"), ("in_memory", "mystore")):
    declare_pair(*_pair, IDENTITY_LOSSES)
```

```bash
pip install "memcp-server[dev]"
MEMCP_CONFORMANCE_EXTRA=mystore=my_adapter.backend:MyBackend \
MEMCP_CONFORMANCE_BACKENDS=mystore,in_memory \
  python -m memcp.conformance
```

The factory takes no arguments and returns a fresh `MemoryBackend`. Read
configuration from the environment inside it.

Notes on getting this right:

- **Declaring the pair is not optional.** Without it the round trip fails with
  `UndocumentedPairError`. It never skips — an undeclared pair is a missing record,
  not an absent feature.
- **`IDENTITY_LOSSES` is the usual answer.** Ids, `created_at`, `updated_at` and
  `history` do not survive any migration, because import calls `add()` on the target.
  If your backend loses something else, add a `Loss(aspect, reason)` — except
  `content` and `scope`, which cannot be declared lost at all.
- **A declared loss the pair cannot measure is reported, not failed.** `history` is
  only comparable when both backends declare `memory_history`; the report lists it as
  `unverified here` so the gap is visible.
- **Narrowing capabilities by omission is not enough.** If you subclass another
  backend and return a smaller `capabilities()` set, override the methods you dropped
  to raise `NotImplementedError`. The suite checks that an undeclared method refuses.
- **Publishing the loss set is your job.** Generate your own document with
  `render_markdown(registered_pairs())` from `memcp.conformance.portability`; this
  repository's `docs/portability.md` covers only the backends it ships.

`tests/test_conformance_out_of_tree.py` drives exactly this recipe in a subprocess
from a scratch directory, so it stays true.

## What the suite covers

- The six abstract methods, plus `capabilities()` and `scope_keys()`.
- Tenant isolation on every method that takes a tenant: search, get, update, delete,
  delete_all, list, history and entities.
- Each of the five optional capabilities, or a check that the undeclared method
  raises `NotImplementedError`.
- A cross-backend round trip per ordered pair of selected backends, asserted against
  the documented loss set.

## What it does not cover

- **Fact extraction quality.** `add(infer=True)` is checked for a well-formed return
  value only. What a backend chooses to extract is the backend's judgement, and it
  needs a real LLM to exercise; CI has none by design (`ci/mem0/README.md`).
- **Ranking quality.** Retrieval is asserted as "the same query still returns this
  memory", not as an order or a score. The in-memory backend ranks by word overlap
  and the CI mem0 stack embeds token hashes, so neither would support a ranking
  claim.
- **Concurrency and load.** One tenant, one caller, one event loop.
- **The live deployment.** `brain-mcp.jartan.dev` is configured separately and is not
  built from `main` unmodified.

## The tool contract

Separate gate, same idea: `docs/tool-surface.json` freezes the 12 MCP tool names,
their argument schemas and their behaviour annotations, and `tests/test_conformance_meta.py`
fails on any drift. Descriptions are excluded on purpose — they are prose for the
model, not contract. After an intentional change, state what it breaks for a
connected client, then run `python -m memcp.toolsurface --write`.
