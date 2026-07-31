# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Import dedup is now scope-aware: same content in different scopes is no longer treated as a duplicate (#30)

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

[0.1.1]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.0
