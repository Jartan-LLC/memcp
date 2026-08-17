# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`memcp up` provisions the memory backend as well as memcp.** One command on a machine with Docker creates the whole stack, waits for it to be healthy, and exits non-zero if it is not inside `--timeout`. `memcp plan` prints every container, volume, port, environment variable and file first, and creates nothing; `memcp verify` stores and retrieves one memory over MCP and times it; `memcp down` stops it and keeps the memories unless `--volumes`; `memcp rotate-token` mints a new bearer token. `docs/deployment.md` covers all of it. (JAR-383)
- **`sqlite` backend** — durable local storage with no API key and no account, so a first install reaches a brain that survives a restart without a provider bill. Retrieval is word overlap and `add_memory` stores content verbatim; both are documented rather than approximated. It passes the same conformance suite as mem0, and round-trips with every other backend with the same four identity losses.
- `--llm-base-url` points the mem0 backend's LLM and embedder at any OpenAI-compatible endpoint (Ollama, llama.cpp, LiteLLM), which removes the provider account from that stack too.
- `MEMCP_ALLOWED_HOSTS` sets an explicit Host header allow-list for DNS-rebinding protection. The MCP SDK turns that protection on by itself **only** when `MEMCP_HOST` is `127.0.0.1`, `localhost` or `::1`, and leaves it off for every other value including `0.0.0.0` — memcp's default bind. Provisioned deployments set it.
- Tenant isolation is now tested end to end through the MCP surface: two tokens, two identities, and assertions that one can neither read, change, delete nor enumerate the other's memories through every registered tool, on every backend.
- The `provision` and `provision_mem0` CI jobs run `memcp up` on a clean runner on every pull request, publish the time to first memory over MCP, assert a second `up` neither rotates the token nor loses memories, and assert the minted token reaches no tracked file.
- Backend conformance suite (`python -m memcp.conformance`), shipped inside the package so an out-of-tree adapter can run it with `pytest --pyargs memcp.conformance.suite`. Reports per backend which capabilities it implements and which it does not; a declared capability that fails its tests fails the run rather than skipping. Register other adapters with `MEMCP_CONFORMANCE_EXTRA`. (#25)
- Cross-backend migration in `memcp.migrate`, now the single implementation behind `export_memories` and `import_memories`. Adds `migrate(source, target, user_id, target_user_id=...)` for moving one tenant between backends. (#27)
- Cross-backend round trip in the conformance suite: 24 memories across 3 scopes move between every pair of selected backends, asserting content, scope and retrieval by the original query survive. What does not survive is declared per pair in `docs/portability.md`, generated from `memcp/conformance/portability.py` and asserted against — an undocumented loss fails, and so does a declaration that no longer holds. `content` and `scope` cannot be declared lost.
- Self-contained mem0 for CI (`ci/mem0/up.sh`): real mem0 and real pgvector with no external API secret, using a standard-library OpenAI-compatible stand-in for embeddings. The conformance suite and round trip now run against it on every pull request. `ci/mem0/README.md` states what that proves and what it does not. (#28)
- `docs/tool-surface.json` freezes the 12 MCP tool names, argument schemas and behaviour annotations; a test fails on any drift. Regenerate deliberately with `python -m memcp.toolsurface --write`.

### Fixed
- The conformance suite runs out of tree. It relied on `asyncio_mode = "auto"` from this repository's `pyproject.toml`, so against an adapter in another repository every test errored with "requested an async fixture 'backend'" or "async def functions are not natively supported". Fixtures now use `@pytest_asyncio.fixture` with an explicit loop scope and each suite module carries `pytestmark = pytest.mark.asyncio`, which is correct under pytest-asyncio's default strict mode. The marker was previously added from `pytest_collection_modifyitems`, which runs too late to have any effect.
- An out-of-tree adapter can declare its portability pairs. `portability.declare_pair()` registers a pair at import time from the module `MEMCP_CONFORMANCE_EXTRA` names; previously the only way was to edit `memcp/conformance/portability.py`, a file such an adapter has installed under `site-packages`, so its round trip failed with `UndocumentedPairError` permanently. `IDENTITY_LOSSES` is public for reuse and `render_markdown(pairs=...)` will generate the adapter's own document. `tests/test_conformance_out_of_tree.py` drives the documented recipe in a subprocess so both fixes stay fixed.
- A declared loss the pair cannot measure no longer fails as stale. `history` is only comparable when both backends declare `memory_history`; it is now reported as `unverified here` instead, so the gap is visible rather than either failing or vanishing.
- mem0 `list_memories` now sends `top_k` explicitly. mem0's `GET /memories` defaults it to 20, so list, export and the import dedup index silently saw only the first 20 memories of a tenant. The adapter now requests mem0's own ceiling of 1000 and logs when a tenant reaches it.

### Changed
- **memcp refuses to start unauthenticated on an interface another machine can reach.** With no `MEMCP_AUTH_TOKENS` every request is served as one tenant called `default_user`, which is a dev server on loopback and an open memory store anywhere else. The startup error names both ways out. The default bind is still `0.0.0.0`, so a deployment that already sets a token is unaffected. (SEC-2026-0059)
- Every image a deployment pulls is pinned by digest, including the Dockerfile's Python base. Moving a pin is a commit in `memcp/deploy/images.py`.
- The Docker image creates `/data` owned by the runtime user, so the sqlite backend can write to a mounted volume as a non-root process.
- Import dedup is scope-aware: identical content in a different scope is a distinct memory, not a duplicate. Previously the same fact stored under two `agent_id` values collapsed to one on import. Tool names and argument shapes are unchanged; `import_memories`' behaviour and description are. (#30)
- uv replaces pip as the installer everywhere — CI, the devcontainer and the Dockerfile — and `uv build` replaces `python -m build` on the publish path. One pin, `uv==0.12.5` in `ci/requirements.txt`, read by every `setup-uv` step via `version-file` and grepped by `.devcontainer/setup.sh`, so Dependabot's `/ci` entry moves every environment at once. No `uv.lock`, no `uv sync`: uv is the installer, not the project manager.
- Migrated to MCP Python SDK 2.0: `mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`. The SDK removed the `fastmcp` module in 2.0, which broke `main` with no change to it. `host`/`stateless_http` moved from the constructor to `streamable_http_app()`; `port` was never used (uvicorn binds, in `__main__`).
- Every dependency now carries an upper bound, runtime and dev. CI installs unpinned on each run, so an unbounded floor let an upstream major redden CI on its release day.

### Added
- CI runs on push to `main` and weekly on a schedule, not on pull requests alone. Dependency drift is now found by CI rather than by the next unrelated PR.

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
