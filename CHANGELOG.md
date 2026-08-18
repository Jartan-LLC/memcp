# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] — 2026-08-18

Backport of the MCP SDK 2.0 fix from `main` (commit `7613adc`). 0.1.1 does not
install: `mcp` was declared `>=1.0` with no ceiling, so a fresh install resolves
to `mcp` 2.x, which removed the `mcp.server.fastmcp` module that `memcp/server.py`
imported. The server exits at import with `ModuleNotFoundError`.

### Fixed
- Migrated to MCP Python SDK 2.0: `mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`. `host`/`stateless_http` moved from the constructor to `streamable_http_app()`; `port` was never used (uvicorn binds, in `__main__`).
- Every dependency now carries an upper bound, runtime and dev. Installs are unpinned, so an unbounded floor let an upstream major break a build with no change here.

### Added
- CI runs on push to `main` and `release/**`, on pull requests into either, and weekly on a schedule. Dependency drift is now found by CI rather than by a deploy.

## [0.1.1] — 2026-06-14

### Fixed
- Accept header workaround for Claude Code and other clients that omit `text/event-stream` (anthropics/claude-code#45368)

## [0.1.0] — 2026-06-14

Initial release.

### Added
- MCP tools: add_memory, search_memory, delete_memory, delete_all_memories, memory_status, export_memories, import_memories, get_memory, update_memory, list_memories, memory_history, memory_entities
- Backend-agnostic architecture with `MemoryBackend` Protocol
- mem0 REST API adapter with tenant isolation (fetch-then-verify ownership)
- In-memory backend for dev/testing (`MEMCP_BACKEND=in_memory`)
- Multi-tenant auth via `MEMCP_AUTH_TOKENS` (token:user_id mapping)
- Pluggable auth via Resolver Protocol (static tokens now, DB/JWT planned)
- Import with dedup and conflict resolution (`on_conflict`: skip, overwrite, duplicate)
- Export with truncation for large memory pools
- Input validation: content/query length, limit bounds, threshold range, scope key/type/size
- Canonical error objects with retry semantics and standard error codes
- `/health` endpoint (pings backend, returns 200/503)
- Structured JSON/plain logging with per-request tenant context
- Constant-time token comparison, scope injection protection, non-ASCII token handling
- Multi-stage Dockerfile + docker-compose
- CI pipeline with ruff, pyright, pytest, Docker build verification
- PyPI and Docker image publish workflows (on tag push)
- Backend selection via `MEMCP_BACKEND` (mem0, in_memory)
- Server config: `MEMCP_HOST`, `MEMCP_PORT`, `MEMCP_LOG_LEVEL`, `MEMCP_LOG_FORMAT`

[0.1.2]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.0
