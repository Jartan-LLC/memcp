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
needs memcp as a dependency and nothing else:

```bash
pip install "memcp-server[dev]"
MEMCP_CONFORMANCE_EXTRA=mystore=my_adapter.backend:MyBackend \
MEMCP_CONFORMANCE_BACKENDS=mystore \
  python -m memcp.conformance
```

The factory takes no arguments and returns a fresh `MemoryBackend`. Read
configuration from the environment inside it.

To take part in the cross-backend round trip, the adapter also needs a row in
`memcp/conformance/portability.py` — see `docs/portability.md`. Until it has one, the
round trip for that pair fails with `UndocumentedPairError` rather than passing
silently.

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
