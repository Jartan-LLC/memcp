# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Backend-agnostic package structure with `MemoryBackend` ABC
- mem0 REST API adapter with fetch-then-verify tenant isolation
- In-memory backend for dev/testing
- Multi-tenant auth via `MEMCP_AUTH_TOKENS` (token:user_id mapping)
- Resolver Protocol for pluggable auth backends
- Per-request tenant identity via contextvars
- MCP tools: add_memory, search_memory, delete_memory, delete_all_memories, memory_status, export_memories, get_memory, update_memory, list_memories, memory_history, memory_entities
- Scope key validation against backend-declared keys
- Input validation (content length, query length, limit min/max, threshold range, scope size/types, whitespace rejection)
- Nested boolean filter rejection with canonical error
- Canonical error objects with retry semantics
- Startup validation for malformed `MEMCP_AUTH_TOKENS`
- `/health` endpoint (pings backend, returns 200/503)
- Structured JSON/plain logging with tenant context
- Log injection protection in plain formatter
- Dockerfile + docker-compose for deployment
- CI pipeline (ruff, pyright, pytest, Docker build + health check)
- PyPI + Docker image publish workflows
- Test suite: conformance, tool layer, auth, mock adapter, tenant isolation, integration

### Changed
- Replaced single `SHIM_AUTH_TOKEN` + `MEM0_USER_ID` with `MEMCP_AUTH_TOKENS`
- All env vars for server config prefixed with `MEMCP_`

### Security
- Constant-time token comparison (hmac.compare_digest)
- user_id stripped from scope dicts (security invariant)
- Fetch-then-verify ownership on all single-ID mem0 operations (get, update, delete, history)
- Post-filter entities endpoint for tenant isolation
- Non-ASCII bearer token handling (decode with replacement, no crash)
- Fork PR rejection + bot blocking in Claude workflow
- Scope value type restriction (strings/numbers only)
