# memcp

Backend-agnostic, multi-tenant MCP memory server. Python, MCP Python SDK 2.0, deployed behind a reverse proxy.

## Rules

The project rules live in `GUARDRAILS.md`, ranked by how firmly each holds; this import
loads them into every session:

@GUARDRAILS.md

## Corrections

- The server class is `mcp.server.mcpserver.MCPServer` (SDK 2.0). It was `mcp.server.fastmcp.FastMCP` on SDK 1.x — that module is gone in 2.0, and the standalone `fastmcp` package was never what this uses
- Every dependency in `pyproject.toml` carries an upper bound. CI installs unpinned, so an unbounded floor lets an upstream major redden `main` on its release day
- mem0 self-hosted REST API does NOT support nested boolean filters (AND/OR/NOT) — they 502
- mem0 self-hosted list endpoint does NOT filter by metadata and does NOT paginate
- mem0 PUT /memories/{id} returns `{"message": "..."}`, not the memory — must GET after PUT
- mem0 GET /entities does NOT filter by user_id — server post-filters for tenant isolation
- mem0 single-ID endpoints (GET/PUT/DELETE/history) are global — adapter does fetch-then-verify for ownership
- The MCP SDK enables DNS-rebinding protection only when the `host` passed to `streamable_http_app` is `127.0.0.1`, `localhost` or `::1`. Every other value, including `0.0.0.0`, leaves Host and Origin unvalidated — set `MEMCP_ALLOWED_HOSTS` to turn it on
- `memcp up` is an operator command, never something the running server does. Nothing it generates may mount the Docker socket
- Only `mem0` declares `memory_entities`. `sqlite` and `in_memory` have no graph and must not claim one — `memory_status` is what agents read, so a capability list has to match the prose
- `docs/tool-surface.json` is generated from the full capability set (`ALL_CAPABILITIES`), not from whichever backend declares the most. A backend honestly dropping a capability must not look like the frozen contract shrinking

## Skills

Project conventions live in `.claude/skills/`. Check the relevant skill when working in an unfamiliar area:

- **api-error-patterns** — MCP tool error format, canonical error codes

When adding a new skill, add an entry here.

## Verify

Run `make check` before declaring work done — it runs CI's lint, typecheck, test, build
and audit checks:

```bash
make check
```

Individual targets (`make lint`, `make test`, …) speed up the inner loop; `make help`
lists them. The conformance run against a real mem0 is in `docs/development.md`.
