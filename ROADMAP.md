# memcp Roadmap: v0.1 → v1.0

**Current state (2026-10-16):** v0.2.0. 12 MCP tools, 3 backends (mem0, sqlite, in-memory),
keys with `reader` and `writer` grants on a space, an audit log, export/import with dedup,
and a conformance suite every backend passes.

---

## v0.2 — Spaces, keys and a proven seam (shipped)

**Goal:** Every backend is held to one contract, and access is per key and per space.

- [x] Conformance test suite for backend adapters (`tests/conformance`)
- [x] Cross-backend migration tooling (export from one → import to another)
- [x] Self-contained CI docker-compose for mem0 (no API secrets needed)
- [x] Scope-aware import dedup
- [x] `sqlite` backend — a second in-repository adapter, and the keyless durable path
- [x] Backends declare the partitions they can't keep apart (`validate_partition`)
- [x] Keys file: named keys, hashed tokens, one `reader` or `writer` grant each
- [x] Audit log of every policy decision

---

## v0.3 — Cognee, a fourth backend

Validate the contract against a fundamentally different backend. The conformance suite,
the cross-backend round trip and the CI mem0 stack are Cognee's gates.

**Goal:** All four adapters (mem0, sqlite, in-memory, Cognee) pass identical conformance
tests.

- [ ] Start memcp and its backend with one command (#105)
- [ ] Cognee backend adapter
- [ ] Protocol refinements from friction (one breaking revision allowed)
- [ ] Upstream shims for Cognee-specific limitations
- [ ] memory_entities validation with a real graph backend
- [ ] Batch operations (batch_add in Protocol, backends loop by default)

**Done when:** Cognee adapter passes conformance suite. Protocol changes documented.

---

## v0.4 — Operations

Production readiness for deployments with many spaces.

- [ ] Registry of spaces, keys and grants managed through an API, in place of the keys file (#112)
- [ ] Rate limiting documentation (Traefik config patterns)
- [ ] Metrics endpoint or structured metrics logging
- [ ] Load test with 10 concurrent spaces
- [ ] Readiness vs liveness probe separation
- [ ] Pagination cursor improvements (opaque tokens, not plain offsets)

**Done when:** Deployed with 5+ real spaces. No operational surprises.

---

## v0.5 — Developer Experience

Make it trivial for community contributors to build backend adapters.

- [ ] Conformance test package (pip-installable, run against any MemoryBackend) (#107)
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

- Admin REST API (stats, audit, key management, bulk operations)
- Server-rendered HTML dashboard (read-only)
- TTL / expiration support
- Self-service user signup (env-var gated)

### v1.2 — Enterprise

- Pluggable auth middleware (JWT/OIDC)
- Relations beyond `reader` and `writer`, such as delete-only or admin
- Interactive dashboard (key management, memory browser)
- OpenTelemetry traces
- SDK / client library scaffold

---

## Deferred Decisions

| Decision | When | Notes |
|---|---|---|
| Graph memory (enable_graph toggle) | v0.3 | Depends on Cognee adapter |
| delete_entities (cascade delete) | v0.3 | Cleaner than overloading delete_all |
| Several auth sources at once (JWT and keys) | v1.2 | Enterprise multi-auth |
| `messages` param on add_memory | Unlikely | Multi-turn input is mem0-specific; breaks backend agnosticism |
| In-memory backend vector search | v0.4+ | Lightweight semantic search (e.g. sentence-transformers) to replace keyword matching |
| Idempotency key on add_memory | v0.4+ | Prevent duplicate storage from retry loops |
| Nested boolean filter support | Never unless mem0 fixes upstream | Rejected by design (502s) |
| Server-side deduplication | Backend concern | mem0 does it, others may not |
