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
- 11 MCP tools: add, search, delete, delete_all, status, export, get, update, list, history, entities
- Scope key validation against backend-declared keys
- Input validation (content length, query length, limit bounds, scope size)
- Nested boolean filter rejection with canonical error
- Canonical error objects with retry semantics
- `/health` endpoint (pings backend, returns 200/503)
- Structured JSON/plain logging with tenant context
- Log injection protection in plain formatter
- Dockerfile + docker-compose for deployment
- CI pipeline (ruff, pyright, pytest)
- PyPI + Docker image publish workflows
- 134 tests (conformance, tool layer, auth, mock adapter, tenant isolation, integration)

### Changed
- Replaced single `SHIM_AUTH_TOKEN` + `MEM0_USER_ID` with `MEMCP_AUTH_TOKENS`
- All env vars for server config prefixed with `MEMCP_`

### Security
- Constant-time token comparison (hmac.compare_digest)
- user_id stripped from scope dicts (security invariant)
- Fetch-then-verify ownership on all single-ID mem0 operations
- Post-filter entities endpoint for tenant isolation
