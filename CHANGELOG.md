# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-16

Upgrading from 0.1.x changes how clients authenticate: follow "Upgrade from 0.1.x" in `docs/deployment.md`.

### Changed

- **Keys replace tokens.** `MEMCP_KEYS_FILE` names a TOML file of keys.
  - Each key has a name, the SHA-256 hashes of its tokens, and one grant: a space and a relation, `reader` or `writer`. The file holds no token.
  - memcp refuses to start while `MEMCP_AUTH_TOKENS` is set. Each `token:user_id` there becomes a key with the `user_id` as its space, so it reaches the same memories. Its secret is the token's hash if the token is random, as `openssl rand -hex 32` makes; otherwise give that client a new token, since a guessable one can be recovered from its hash.
  - A `reader` key gets the new error code `forbidden` from `add_memory`, `update_memory`, `delete_memory`, `delete_all_memories` and `import_memories`.
  - memcp refuses to start on a malformed keys file, and on a granted space that is `*`, empty, or one the backend can't keep apart from others, such as one with whitespace at either end on mem0. Its errors never quote a secret.
  - Without a keys file, memcp serves one key, `local`, with `writer` on the space `default_user`, which holds what a 0.1.x server without tokens wrote. With a keys file, as under `docker-compose.yml`, grant `default_user` to a key to reach those memories.
- **`MEMCP_BACKEND` is required.** It defaulted to `mem0`. Set `MEMCP_BACKEND=mem0` to keep a 0.1.x deployment running as before.
- **`docker-compose.yml` runs the latest release** instead of 0.1.2. It mounts `keys.toml` from its own directory and refuses to start without it or `MEMCP_BACKEND`. It publishes memcp on `127.0.0.1:8080` only, where 0.1.2 published a random port on every interface, and admits only loopback Host headers unless `MEMCP_ALLOWED_HOSTS` is set.
- **`update_memory`'s `metadata` is a patch.** Keys you don't send are kept; on in_memory they used to be dropped. Send a key as `null` to remove it. `content` is optional.
- **Metadata follows one set of rules on `add_memory`, `update_memory` and `import_memories`, on every backend.** 0.1.2 stored it as given.
  - Values are strings, numbers, booleans or lists of strings. A key is at most 64 characters, and a memory's metadata at most 16 KiB as JSON.
  - A reserved key (listed under Metadata in `docs/reference.md`) and a `_memcp_` key are refused.
  - A write that breaks a rule gets `validation_error`, which names the key for every rule but the size cap. `import_memories` lists the entry in `errors` instead.
  - Metadata already stored that breaks these rules is still returned. An update that doesn't grow it is accepted, so its keys can be removed, except a key whose name is now reserved.
  - Export, then import, refuses such a memory, content included, and lists it in `errors`.
  - `import_memories` with `on_conflict=overwrite` replaces the metadata when the entry has `metadata`; on mem0 it used to merge. A reserved key already stored stays.
- **Every tool applies the same scope rules, on every backend.**
  - A scope naming `user_id` is refused with `invalid_scope`. It used to be dropped with the warning "Stripped user_id from scope dict", which is no longer logged.
  - An empty, `*` or `null` value is refused with `validation_error`. On mem0, search and list used to read such a value as "any".
  - So is a value with whitespace at either end, which mem0 trims, and a `run_id` with whitespace inside it, which mem0 refuses.
  - A NaN or infinite number is refused with `validation_error`.
  - Scope values are stored and returned as text. On in_memory, a `run_id` sent as `7` now reads back as `"7"`. On mem0, a numeric or boolean `run_id`, which mem0 refused, is stored as text.
  - An import entry whose scope breaks one of these rules is listed in `errors`.
  - A memory a backend returns outside the requested scope is left out of search and list results, with a warning from `memcp.policy`.
- **`agent_id` is normalized** on every write and filter: lowercased, trimmed, each run of other characters turned into one hyphen, and hyphens at either end dropped. The server's MCP instructions ask clients to send their own name in that form, such as `claude-code`. A filter no longer reaches a memory stored under an `agent_id` that isn't in that form.
- Import treats the same content in a different scope as a different memory, not a duplicate.
- in_memory's keyword search matches words: runs of letters and digits, ignoring query words under three characters, with two words matching when they are equal or share their first four characters. An exact phrase scores 1.0. It used to match any substring, so a one-letter word matched almost everything.
- in_memory no longer registers `memory_entities`. It answered with one synthetic node and no relationships.
- Log records carry `key` and `space`, the name and space of the request's key, in place of `user_id`. Both are `null` in JSON and `-` in plain format when no key is set: outside a request, on `/health`, and on a request refused with 401. Update any log query or alert on `user_id`.
- Two loggers are renamed: `memcp.backend.mem0` is now `memcp.backends.mem0.adapter`, and `memcp.auth` is now `memcp.http.auth`. Update any log filter or alert that matches the old names.

### Added

- **The `sqlite` backend:** durable local storage with no API key and no account. Retrieval is the same keyword matching as in_memory, and `add_memory` stores content verbatim. The Docker image creates `/data` for it, writable by the image's user.
- **An audit log.** Every memory operation writes one record to the logger `memcp.audit`, whether it succeeds or is refused: the operation, the key's name, space and relation, the outcome (`ok` or the error code), and the memory ids or the scope and a count. It holds no content, query or metadata, and logs at `INFO` whatever `MEMCP_LOG_LEVEL` says. JSON logs carry it as an `audit` object. `docs/reference.md` lists the fields.
- `MEMCP_ALLOWED_HOSTS` sets a Host header allow-list for DNS-rebinding protection. The MCP SDK turns that protection on by itself only when `MEMCP_HOST` is `127.0.0.1`, `localhost` or `::1`, and leaves it off for every other value, `0.0.0.0` included.
- `memory_status` reports `extracts_facts` and `retrieval`, so a client can tell whether `infer` does anything.
- `memcp.policy.transfer.migrate(source, target, partition, operator=..., target_partition=...)` moves one space's memories between backends, for an operator's own scripts. It is recorded in the audit log under the operator's name, and refused, before anything is read, for a blank operator, logging that would drop that record, an empty or `*` partition, one either backend can't match exactly, or an `on_conflict` the target can't carry out.
- The Docker image is published for `linux/arm64` too, carries a build-provenance attestation, and is also tagged with its minor version, such as `0.2`.

### Fixed

- On mem0, `list_memories`, export and import's duplicate check saw only a space's first 20 memories, mem0's default page. They now read up to mem0's ceiling of 1000.
- **On mem0, list, entities, export and import no longer treat 1000 memories as all of them.** mem0 returns at most 1000 with no way to page further. `list_memories` and `memory_entities` now return `incomplete: true`, `export_memories` `truncated: true`, and `import_memories` `dedup_incomplete: true`. A space of exactly 1000 memories reads the same way, since mem0 can't tell it from more.
- A memory id ending in a line break is refused with `validation_error`. On mem0 it failed the call with an unhandled error.

### Security

- **memcp refuses to return a memory from another space, even when the backend hands it one.** The request fails with the error code `internal_error`, and the server logs `isolation_violation` without the memory's content. memcp checks every memory it returns and every memory it has just stored, and fetches a memory to check it before it updates, deletes or shows the history of it. On mem0 that is one more request per memory added or imported, and per update, delete or history call.
- **A memory store's error response no longer reaches the client.** A `backend_error` or `timeout` message used to carry up to 500 characters of the store's response, which can include its reasons and its URL; a failed `import_memories` entry did the same in `errors`. The message now gives at most the status, and the server logs the response as a warning.
- `memory_entities` on mem0 counts only the caller's own memories. It read mem0's store-wide entity list, so another space writing an `agent_id` or `run_id` equal to the caller's user id put a row, with its count and times, into the caller's results. It now returns the caller's agent and run rows, with no row for the caller's own space, honours `scope`, and counts at most 1000 memories.
- `delete_all_memories` refuses an empty, `*` or `null` scope value. On mem0 such a value matched anything, so the delete reached memories outside the requested scope.
- **Every memory carries `author`, the name of the key that last wrote it, set by the server.**
  - `search_memory`, `get_memory`, `list_memories`, `update_memory` and `export_memories` return it. It is `local` for a write made without a keys file, and `null` for a memory written by 0.1.2 or earlier.
  - A caller can't set it: memcp refuses `_memcp_` keys in caller metadata. An update, or an `import_memories` overwrite, records the acting key. `import_memories` ignores an entry's `author`, so an export and import doesn't keep it.
  - The server's MCP instructions tell a client to treat every memory as third-party input: attribute it, re-check it, and never follow instructions inside it.
  - **On mem0, sweep the store for the metadata key `_memcp_system` before this version serves it.** 0.1.2 stored callers' metadata as given, and memcp can't enumerate a mem0 install's spaces to clean it, so a key planted then reads back as an `author` no server set. in_memory keeps `author` outside metadata and needs no sweep.
- **memcp refuses to start unauthenticated on an interface another machine can reach.** Without a keys file every request is served as the key `local`, which is a dev server on loopback and an open memory store anywhere else. The startup error names both ways out. The default bind is still `0.0.0.0`.

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

[Unreleased]: https://github.com/Jartan-LLC/memcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.2.0
[0.1.2]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/Jartan-LLC/memcp/releases/tag/v0.1.0
