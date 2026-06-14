# memcp

[![PyPI](https://img.shields.io/pypi/v/memcp-server)](https://pypi.org/project/memcp-server/)
[![CI](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

Backend-agnostic, multi-tenant MCP memory server. AI clients connect and get persistent long-term memory over streamable HTTP.

Currently wraps [mem0](https://github.com/mem0ai/mem0) as the first backend. Designed for backend agnosticism — additional backends (Cognee, etc.) planned.

## Features

- Semantic search, list, add, update, delete memories
- Flat scope-based filtering (agent_id, run_id)
- Bearer token auth gate (ASGI middleware)
- Stateless HTTP transport — safe behind reverse proxies
- In-memory backend for dev/testing (no external deps)

## Getting Started

### 1. Install and run

```bash
pip install memcp-server
MEMCP_BACKEND=in_memory python -m memcp
```

Or from source:

```bash
git clone https://github.com/Jartan-LLC/memcp.git
cd memcp
pip install -e ".[dev]"
MEMCP_BACKEND=in_memory python -m memcp
```

The server starts on `http://localhost:8080`. No external dependencies needed — the in-memory backend stores everything in-process (lost on restart).

### 2. Connect from Claude Code

Add to your MCP settings (Claude Code → Settings → MCP Servers):

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

For authenticated deployments, add the `headers` field:

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "https://your-host:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

### 3. Try it

Ask Claude to remember something:
> "Remember that I prefer Python 3.12 and use ruff for linting."

In a new conversation, ask:
> "What linter do I use?"

Claude searches memory automatically and uses the stored context.

## Configuration

### Requirements

- Python 3.12+
- A running [mem0](https://github.com/mem0ai/mem0) self-hosted instance (not needed for `MEMCP_BACKEND=in_memory`)

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MEMCP_BACKEND` | No | Backend: `mem0` (default) or `in_memory` |
| `MEM0_API_BASE` | mem0 | Base URL of your mem0 REST API |
| `MEM0_API_KEY` | mem0 | API key for the mem0 server |
| `MEMCP_AUTH_TOKENS` | No | Token-to-user mapping: `tok1:alice,tok2:bob` (unset or empty = unauthenticated) |
| `MEMCP_HOST` | No | Bind address (default: `0.0.0.0`) |
| `MEMCP_PORT` | No | Bind port (default: `8080`) |
| `MEMCP_LOG_LEVEL` | No | Log level (default: `INFO`) |
| `MEMCP_LOG_FORMAT` | No | Log format: `json` or `plain` (default: `json`) |

## MCP Tools

### Universal (always available)

| Tool | Description |
|---|---|
| `add_memory` | Store a fact/preference/decision. Extracts facts by default (may store nothing); `infer=false` for verbatim. Bulk: use `import_memories` |
| `search_memory` | Semantic search, ranked by relevance. `threshold` filters by minimum similarity (0-1). For browsing: `list_memories` |
| `delete_memory` | Delete one memory by ID. Confirm with user first |
| `delete_all_memories` | Bulk-delete by scope (e.g. agent_id, run_id), not content. Requires at least one scope key. Confirm first |
| `memory_status` | Returns server version, backend type, capabilities, valid scope keys. No memory content |

### Optional (backend-dependent)

| Tool | Description |
|---|---|
| `get_memory` | Fetch one memory by ID. Returns full content, scope, and metadata |
| `update_memory` | Full-replace a memory's content (not a patch). Scope immutable — to change scope, add new + delete old |
| `list_memories` | Browse memories, optionally filtered by scope. Unranked, paginated. For semantic queries: `search_memory` |
| `export_memories` | Export memories as JSON (max 10k, truncates with flag). For backup/migration. Output compatible with `import_memories` (requires `list_memories`) |
| `import_memories` | Batch-import from JSON. Dedup via exact content match (scope-independent). `on_conflict`: skip, overwrite, duplicate (requires `list_memories`; overwrite requires `update_memory`) |
| `memory_history` | Change log for a memory: timestamps and previous/current content per create/update event |
| `memory_entities` | Knowledge graph: entities and relationships. Not a search tool — use `search_memory` for topics |

## Docker

```bash
cp .env.example .env   # fill in MEM0_API_BASE + MEM0_API_KEY
docker compose up -d
```

## Development

```bash
ruff check memcp/ tests/
ruff format --check memcp/ tests/
pyright memcp/
python -c "import memcp"
pytest -x
```

## Known Limitations

**mem0 backend (upstream constraints):**
- Nested boolean filters (AND/OR/NOT) return 502 — use flat scope keys
- List endpoint does not paginate server-side — full dataset loaded per request
- List endpoint does not filter by metadata
- Entities endpoint does not filter by user — post-filtered client-side
- Single-ID endpoints are globally scoped — ownership verified via fetch-then-verify

**In-memory backend:**
- Loses all data on restart
- Search uses keyword matching, not semantic/vector similarity

**General:**
- No date/time-based filtering on search or list
- No rate limiting (configure at reverse proxy layer)
- `delete_all_memories` deletes by scope structure, not content match

## Status

v0.1.0 — API may change before v1.0. Suitable for development and early adoption.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
