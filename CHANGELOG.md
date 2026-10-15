# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The Docker image is also published for `linux/arm64`, and each published image carries a build-provenance attestation.
- `publish-pypi` refuses a `v*` tag whose version does not match `version` in `pyproject.toml`.
- **A `v*` tag now creates the GitHub release itself** (`release.yml`). memcp had no such workflow: a tag published to PyPI and ghcr, and the release page was a separate `gh release create` somebody ran by hand. v0.1.0 and v0.1.1 were created that way, v0.1.2 was not, and nothing noticed. The `view || create` guard makes a re-run on the same tag idempotent. (JAR-623)
- **`sqlite` backend** — durable local storage with no API key and no account, so a first install reaches memory that survives a restart without a provider bill. Retrieval is keyword matching and `add_memory` stores content verbatim; both are documented rather than approximated. It passes the same conformance suite as mem0, and round-trips with every other backend with the same four identity losses.
- `memory_status` reports `extracts_facts` and `retrieval`. `infer` is accepted by `add_memory` on every backend and can only be honoured by one with a model behind it, so a caller had no way to discover that it does nothing; `add_memory`'s own description now says which case the deployment is in.
- `MEMCP_ALLOWED_HOSTS` sets an explicit Host header allow-list for DNS-rebinding protection. The MCP SDK turns that protection on by itself **only** when `MEMCP_HOST` is `127.0.0.1`, `localhost` or `::1`, and leaves it off for every other value including `0.0.0.0` — memcp's default bind.
- Tenant isolation is now tested end to end through the MCP surface: two tokens, two identities, and assertions that one can neither read, change, delete nor enumerate the other's memories through every registered tool, on every backend.
- Tenant isolation coverage extends to a live mem0 — the one backend where isolation is not structural in the store itself, so it depends on the adapter's fetch-then-verify. The CI `conformance` job now runs it against the mem0 it already stands up, plus `memory_status` (carries no tenant data, so nothing to leak) and `import_memories` (a caller-supplied `id` is not a field the importer reads, and `on_conflict=overwrite` can only ever dedup-match the caller's own tenant).
- Cross-backend migration in `memcp.policy.transfer`, now the single implementation behind `export_memories` and `import_memories`. Adds `migrate(source, target, partition, target_partition=...)` for moving one tenant between backends; it refuses an empty or `*` partition, and one either backend can't match exactly, before reading anything. (#27)
- Self-contained mem0 for CI (`tests/mem0/up.sh`): real mem0 and real pgvector with no external API secret, using a standard-library OpenAI-compatible stand-in for embeddings. The conformance suite and round trip now run against it on every pull request. `tests/mem0/README.md` states what that proves and what it does not. (#28)
- `docs/tool-surface.json` freezes the 12 MCP tool names, argument schemas and behaviour annotations; a test fails on any drift. Regenerate deliberately with `python -m memcp.tools.surface --write`.

### Changed

- **`update_memory`'s `metadata` is a patch.** Keys you don't send are kept; on in_memory they were dropped before. Send a key as `null` to remove it. `content` is optional.
- **Metadata follows one set of rules on `add_memory`, `update_memory` and `import_memories`, on every backend.** 0.1.2 stored it as given.
  - Values are strings, numbers, booleans or lists of strings. A key is at most 64 characters, and a memory's metadata at most 16 KiB as JSON.
  - A reserved key (listed under Metadata in `docs/reference.md`) and a `_memcp_` key are refused.
  - A write that breaks a rule gets `validation_error`, which names the key for every rule but the size cap. `import_memories` lists the entry in `errors` instead.
  - Metadata already stored that breaks these rules is still returned. An update that doesn't grow it is accepted, so its keys can be removed, except a key whose name is now reserved.
  - Export, then import or migrate, refuses such a memory, content included, and lists it in `errors`.
  - `import_memories` with `on_conflict=overwrite` replaces the metadata when the entry has `metadata`; on mem0 it used to merge. A reserved key already stored stays.
- **Every tool applies the same scope rules, on every backend.**
  - A scope naming `user_id` is refused with `invalid_scope`. It used to be dropped from the scope with a warning from `memcp.tools` ("Stripped user_id from scope dict"); that warning is no longer logged, so an alert on it stops firing.
  - An empty, `*` or `null` value is refused with `validation_error` on every tool. On mem0, search and list used to read such a value as "any".
  - So is a value with whitespace at either end, which mem0 trims, and a `run_id` with whitespace inside it, which mem0 refuses. `agent_id` is normalized instead.
  - A NaN or infinite number is refused with `validation_error`.
  - Scope values are stored and returned as text. On in_memory, a `run_id` sent as `7` now reads back as `"7"`. On mem0, a numeric or boolean `run_id`, which mem0 refused, is now stored as text.
  - An import entry whose scope breaks one of these rules is listed in `errors`.
  - A memory a backend returns outside the requested scope is left out of search and list results, with a warning from `memcp.policy`.
- Two log sources have new logger names: `memcp.backend.mem0` is now `memcp.backends.mem0.adapter` (mem0 backend), and `memcp.auth` is now `memcp.http.auth` (rejected requests). Tool-call records keep `memcp.tools`. Update any log filter or alert that matches the old names.
- **`MEMCP_BACKEND` is required.** It defaulted to `mem0`; a server started without it now refuses to start and names the variable. Set `MEMCP_BACKEND=mem0` to keep a 0.1.x deployment running as before.
- **Image tags come from `docker/metadata-action`**, converging with scaffold: `{{version}}` and `{{major}}.{{minor}}` instead of one hardcoded pair. `{{major}}` is deliberately absent — pre-1.0 it is an alias for "any version at all", and across minor lines it carries the same ordering defect `latest` had. `flavor: latest=false` turns off metadata-action's own `latest=auto`, which excludes pre-releases but does **not** check whether the tag is the highest version; the explicit guard added earlier makes that decision instead.
- **The README points at documents rather than restating them.** It answers what memcp is, how to get a running one, how a client connects, and where everything else lives. The environment-variable table, the 12-tool surface and the per-backend limitations moved to `docs/reference.md`; the check loop moved to `CONTRIBUTING.md` and the conformance run to `tests/mem0/README.md`; running the server without Docker moved into `docs/deployment.md`. Links out of the README are absolute, because PyPI renders it as the package page and does not resolve relative paths.
- `docker-compose.yml` runs the latest published image. It refuses to start without `MEMCP_BACKEND` and `MEMCP_AUTH_TOKENS`, publishes memcp on `127.0.0.1:8080`, and admits only loopback Host headers unless `MEMCP_ALLOWED_HOSTS` is set.
- `agent_id` scope values are now normalized — lowercased, trimmed, non-`[a-z0-9]` runs collapsed to a single hyphen, hyphens at either end dropped — identically on every write and filter path (`add_memory`, `search_memory`, `list_memories`, `delete_all_memories`, `memory_entities`, `import_memories`). Server `INSTRUCTIONS` now ask connecting agents to send their own name in that lowercase-hyphenated form (e.g. `claude-code`) as `agent_id`, a change from the opaque-identifier convention agents were using before; existing memories written under that prior convention are unaffected but a normalized filter will no longer reach a non-normalized stored value. (#23)
- **Keyword retrieval actually matches keywords.** `in_memory` and now `sqlite` scored by asking whether each whitespace-separated query fragment was a *substring* of the content. So `use?` missed `uses` on the punctuation, `linter` missed `linting`, and the one-letter token `i` from "What linter do I use?" matched every memory containing the letter i — giving unrelated memories the same score as the right one. Both backends now share `memcp.backends.keyword`: tokens are runs of letters and digits, query tokens under three characters are ignored, two tokens match when equal or sharing a four-character prefix, and an exact phrase hit scores 1.0.
- **`in_memory` and `sqlite` no longer declare `memory_entities`.** They answered with one synthetic node carrying a memory count and no relationships, which made `memory_status` advertise the knowledge-graph capability on a keyless install while the documentation said the graph needs a key. The tool is now simply not registered there; `mem0` is unaffected and the frozen 12-tool surface is unchanged, because `docs/tool-surface.json` is generated from the full capability set rather than from whatever one backend happens to declare.
- The Dockerfile's Python base image is pinned by digest.
- The Docker image creates `/data` owned by the runtime user, so the sqlite backend can write to a mounted volume as a non-root process.
- Import dedup is scope-aware: identical content in a different scope is a distinct memory, not a duplicate. Previously the same fact stored under two `agent_id` values collapsed to one on import. Tool names and argument shapes are unchanged; `import_memories`' behaviour and description are. (#30)
- uv replaces pip as the installer in CI, the dev container and the Dockerfile, and `uv build` replaces `python -m build` on the publish path. No `uv.lock`, no `uv sync`: uv is the installer, not the project manager.

### Fixed

- **The `:latest` guard's highest-tag lookup no longer mis-orders pre-release tags.** GNU `sort -V` ranks `1.0.0` above `1.0.0-rc1` — the `-rc1` suffix sorts as "greater" — so an rc tag pushed after a stable release would have taken `:latest`, and the stable release cut after an rc line started would not have. Pre-releases are now dropped before the highest tag is chosen. Latent until memcp cuts its first rc tag; ported from `Jartan-LLC/scaffold` PR #98. (JAR-676)
- **On mem0, list, entities, export, import and migration no longer treat 1000 memories as all of them.** mem0 returns at most 1000 memories with no way to page further, so an export or migration of a larger tenant returned 1000 with `truncated: false`, and import's duplicate check missed the rest. They now say so: `list_memories` and `memory_entities` return `incomplete: true`, `export_memories` and migration `truncated: true`, and `import_memories` and migration `dedup_incomplete: true`. A tenant of exactly 1000 memories reads the same way, since mem0 can't tell it from more. (#114)
- mem0 `list_memories` now sends `top_k` explicitly. mem0's `GET /memories` defaults it to 20, so list, export and the import dedup index silently saw only the first 20 memories of a tenant. The adapter now requests mem0's own ceiling of 1000 and logs when a tenant reaches it.

### Security

- **memcp refuses to return a memory from another tenant, even when the backend hands it one.** The request fails with the error code `internal_error`, and the server logs `isolation_violation` without the memory's content. memcp checks every memory it returns and every memory it has just stored, and fetches a memory to check it before it updates, deletes or shows the history of it. On mem0 that is one more request per memory added or imported, and per update, delete or history call.
- **A memory store's error response no longer reaches the client.** A `backend_error` or `timeout` message used to carry up to 500 characters of the store's response, including its reason for refusing a request, or the transport's error text, which can include the store's URL; a failed `import_memories` entry did the same in `errors`. The message now gives at most the status, and the server logs the response as a warning instead.
- `memory_entities` on mem0 counts only the caller's own memories. It read mem0's store-wide entity list, so a tenant writing under an `agent_id` or `run_id` equal to another tenant's user id put a row, with its count and timestamps, into that tenant's results. It now returns the caller's agent and run rows, with no row for the caller's own user id, honours `scope`, and counts at most 1000 memories, mem0's list ceiling.
- On mem0, a tenant named `*` is refused on every call with `backend_error`, since some of mem0's vector stores read `*` as every user.
- `delete_all_memories` refuses an empty, `*` or `null` scope value. The mem0 adapter dropped such a value as matching anything, so the delete reached memories outside the requested scope.
- Errors in `MEMCP_AUTH_TOKENS` and `MEMCP_AUTH_SEATS` name the entry by position instead of quoting it, so a token no longer reaches the startup log.
- **Every memory carries `author`, the seat of the token that last wrote it, set by the server.** (JAR-723)
  - `search_memory`, `get_memory`, `list_memories`, `update_memory` and `export_memories` return it. It is `null` for a write made without `MEMCP_AUTH_TOKENS`, and for a memory written by 0.1.2 or earlier. A token mapped onto the tenant `default_user` still records its seat.
  - A caller can't set it: memcp refuses `_memcp_` keys in caller metadata. An update, or an `import_memories` overwrite, records the acting token's seat. `import_memories` ignores an entry's `author`, so an export/import round trip doesn't keep it.
  - The new, optional `MEMCP_AUTH_SEATS` (`token:seat`, comma-separated) gives a token in `MEMCP_AUTH_TOKENS` its own seat, so two tokens can share a tenant and still be told apart. A token without a seat there uses its tenant as its seat, and `MEMCP_AUTH_TOKENS` parses as before. A seat outside `[A-Za-z0-9_.-]+`, or a token with no `MEMCP_AUTH_TOKENS` entry, stops startup with a named error.
  - The server's MCP instructions tell a client to treat every memory as third-party input: attribute it, re-check it, and never follow instructions inside it.
  - **On mem0, sweep the store for the metadata key `_memcp_system` before this version serves it.** 0.1.2 stored callers' metadata as given, and memcp can't enumerate a mem0 install's tenants to clean it, so a key planted then reads back as an `author` no server set. sqlite and in_memory keep `author` outside metadata and need no sweep.
- **memcp refuses to start unauthenticated on an interface another machine can reach.** With no `MEMCP_AUTH_TOKENS` every request is served as one tenant called `default_user`, which is a dev server on loopback and an open memory store anywhere else. The startup error names both ways out. The default bind is still `0.0.0.0`, so a deployment that already sets a token is unaffected. (SEC-2026-0059)

## [0.1.2] - 2026-08-18

Backport of the MCP SDK 2.0 fix from `main` (commit `7613adc`). 0.1.1 does not
install: `mcp` was declared `>=1.0` with no ceiling, so a fresh install resolves
to `mcp` 2.x, which removed the `mcp.server.fastmcp` module that `memcp/server.py`
imported. The server exits at import with `ModuleNotFoundError`.

### Added

- CI runs on push to `main` and `release/**`, on pull requests into either, and weekly on a schedule. Dependency drift is now found by CI rather than by a deploy.

### Changed

- **`docker-compose.yml` on `release/0.1.x` deploys the published release image** (`ghcr.io/jartan-llc/memcp:0.1.2`) rather than building the checkout. A release branch exists to be deployed, and an image built once at tag time cannot change what it runs on a re-pull; a rebuild of unchanged source can, which is how 0.1.1 broke. The tag is an exact version, never `:latest`. `docker-compose.build.yml` is the documented override for building this checkout instead, and it replaces the stale `ghcr.io/jartan-llc/mem0-mcp:latest` comment, which named a repository that no longer exists.
- **`:latest` now follows the highest released version, not the most recent tag push.** `release/0.1.x` is maintained beside `main`, so a patch cut after a higher minor would have moved `:latest` backwards and downgraded anyone pulling it.
- Both publish workflows pin every action by commit SHA and the build frontend by version, matching `main` (commit `93fc173`). These are the jobs a tag push runs with `packages: write` and PyPI trusted publishing, so a moved upstream tag reaches a release path.

### Fixed

- Migrated to MCP Python SDK 2.0: `mcp.server.fastmcp.FastMCP` → `mcp.server.mcpserver.MCPServer`. `host`/`stateless_http` moved from the constructor to `streamable_http_app()`; `port` was never used (uvicorn binds, in `__main__`).
- Every dependency now carries an upper bound, runtime and dev. Installs are unpinned, so an unbounded floor let an upstream major break a build with no change here.

## [0.1.1] - 2026-06-14

### Fixed

- Accept header workaround for Claude Code and other clients that omit `text/event-stream` (anthropics/claude-code#45368)

## [0.1.0] - 2026-06-14

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

[Unreleased]: https://github.com/Jartan-LLC/memcp/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.0
