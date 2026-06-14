# memcp Roadmap: v0.1 → v1.0

**Current state (2026-06-14):** v0.1.0 shipped. 12 MCP tools, 2 backends (mem0 + in-memory), multi-tenant auth, export/import with dedup.

---

## v0.2 — Third Backend (Cognee)

Validate the Protocol against a fundamentally different backend. mem0 and in-memory already exist.

**Goal:** All three adapters (mem0, in-memory, Cognee) pass identical conformance tests.

- [ ] Cognee backend adapter
- [ ] Protocol refinements from friction (one breaking revision allowed)
- [ ] Upstream shims for Cognee-specific limitations
- [ ] memory_entities validation with a real graph backend
- [ ] Batch operations (batch_add in Protocol, backends loop by default)
- [ ] Cross-backend migration tooling (export from one → import to another)
- [ ] Self-contained CI docker-compose for mem0 (no API secrets needed)

**Done when:** Cognee adapter passes conformance suite. Protocol changes documented.

---

## v0.3 — Operations

Production readiness for multi-tenant deployments.

- [ ] DB-backed token management (SQLite/Postgres, replaces env-var mapping)
- [ ] DBResolver implementing existing Resolver Protocol
- [ ] Rate limiting documentation (Traefik config patterns)
- [ ] Metrics endpoint or structured metrics logging
- [ ] Load test with 10 concurrent tenants
- [ ] Readiness vs liveness probe separation
- [ ] Pagination cursor improvements (opaque tokens, not plain offsets)

**Done when:** Deployed with 5+ real tenants. No operational surprises.

---

## v0.4 — Developer Experience

Make it trivial for community contributors to build backend adapters.

- [ ] Conformance test package (pip-installable, run against any MemoryBackend)
- [ ] Backend adapter development guide
- [ ] CI template for adapter projects
- [ ] Published API reference (auto-generated or manual)
- [ ] MkDocs-Material docs site (if 5+ pages warranted)

**Done when:** External contributor can write and test an adapter using only published docs.

---

## v1.0 — Protocol Freeze

The public release. Protocol stabilization and semver guarantee.

| Criterion | Requirement |
|---|---|
| Protocol stability | 3+ months stable across 3+ backends |
| Conformance | All backends pass same suite, zero skips |
| Documentation | Published Protocol spec + backward-compat policy |
| Semver | Abstract method signatures frozen; changes require major version bump |

- [ ] Protocol frozen
- [ ] Semantic versioning guarantee
- [ ] Community backend adapter guide
- [ ] Comprehensive documentation
- [ ] Consider rebrand (memcp → ?)

**Done when:** Fitness-based freeze — Protocol survived 3+ real adapters without contortion.

---

## Post-v1.0

### v1.1 — Admin & Dashboard
- Admin REST API (stats, audit, token CRUD, bulk operations)
- Server-rendered HTML dashboard (read-only)
- TTL / expiration support
- Self-service user signup (env-var gated)

### v1.2 — Enterprise
- Pluggable auth middleware (JWT/OIDC)
- Scoped permissions (read/write/delete/admin)
- Interactive dashboard (token management, memory browser)
- OpenTelemetry traces
- SDK / client library scaffold

---

## Deferred Decisions

| Decision | When | Notes |
|---|---|---|
| Graph memory (enable_graph toggle) | v0.2 | Depends on Cognee adapter |
| delete_entities (cascade delete) | v0.2 | Cleaner than overloading delete_all |
| ChainResolver (JWT + static simultaneously) | v1.2 | Enterprise multi-auth |
| `messages` param on add_memory | Unlikely | Multi-turn input is mem0-specific; breaks backend agnosticism |
| In-memory backend vector search | v0.3+ | Lightweight semantic search (e.g. sentence-transformers) to replace keyword matching |
| Idempotency key on add_memory | v0.3+ | Prevent duplicate storage from retry loops |
| Scope-aware import dedup | v0.2+ | Currently content-only; same content in different scopes treated as duplicate |
| Strip backend error details from MCP responses | v0.3+ | Currently raw backend messages exposed; sanitize to status code + generic message |
| Nested boolean filter support | Never unless mem0 fixes upstream | Rejected by design (502s) |
| Server-side deduplication | Backend concern | mem0 does it, others may not |
