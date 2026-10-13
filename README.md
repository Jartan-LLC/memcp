# memcp

[![PyPI](https://img.shields.io/pypi/v/memcp-server)](https://pypi.org/project/memcp-server/)
[![CI](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Jartan-LLC/memcp/badge)](https://scorecard.dev/viewer/?uri=github.com/Jartan-LLC/memcp)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/Jartan-LLC/memcp/blob/main/LICENSE)

Backend-agnostic, multi-tenant MCP memory server. AI clients connect and get
persistent long-term memory over streamable HTTP.

## Quickstart

```bash
mkdir memcp && cd memcp
curl -fsSLO https://github.com/Jartan-LLC/memcp/releases/latest/download/docker-compose.yml
curl -fsSL -o .env https://github.com/Jartan-LLC/memcp/releases/latest/download/env.example
chmod 600 .env
# In .env, set MEMCP_AUTH_TOKENS=<token>:<your-name>; generate a token with
#   openssl rand -hex 32
docker compose up -d --wait
```

Needs Docker. memcp runs on `127.0.0.1:8080` with the `sqlite` backend.

**No account, no API key.** Memories go to a file on disk and survive a restart.
Search is keyword matching, so it finds a memory when your question reuses its words
and misses one phrased differently, and it stores what you give it word for word
instead of pulling facts out of it. Semantic search and the knowledge graph need mem0
and a key. A client does not have to guess which it is talking to: `memory_status`
says.

## Connect from Claude Code

With your token (the part of `MEMCP_AUTH_TOKENS` before the colon):

```bash
claude mcp add --transport http memcp http://localhost:8080/mcp \
  --header "Authorization: Bearer YOUR_TOKEN"
```

Behind a reverse proxy, the URL is `https://<your-host>/mcp`. A server started with
`python -m memcp` on loopback and no `MEMCP_AUTH_TOKENS` takes no `--header`.

## Try it

Ask Claude to remember something:

> "Remember that I prefer Python 3.12 and use ruff for linting."

In a new conversation, ask:

> "What linter do I use?"

Claude searches memory automatically and uses the stored context. On the default
`sqlite` backend this works because `linter` and `linting` share a stem — matching is
lexical, so a question phrased in words that do not appear in the memory will not
find it. `mem0` is the backend that matches on meaning.

## What it does

- Durable, keyless local storage (`sqlite`), or mem0 on pgvector for semantic search and a knowledge graph
- Add, search, list, update and delete memories over MCP — 12 tools, frozen surface
- Flat scope-based filtering (`agent_id`, `run_id`)
- Per-tenant bearer token auth, minted per deployment, never defaulted
- Stateless HTTP transport — safe behind reverse proxies

## Documentation

| Document | What it covers |
|---|---|
| [deployment.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/deployment.md) | Running memcp with Docker Compose — backends, tokens, reverse proxies, and running the server without Docker |
| [reference.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/reference.md) | Environment variables, the 12 MCP tools, and known limitations per backend |
| [conformance.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/conformance.md) | Holding a `MemoryBackend` to the suite, including one in another repository |
| [CONTRIBUTING.md](https://github.com/Jartan-LLC/memcp/blob/main/CONTRIBUTING.md) | Working on memcp itself — setup and the check loop |
| [tests/mem0/README.md](https://github.com/Jartan-LLC/memcp/blob/main/tests/mem0/README.md) | Running the conformance suite against a real mem0 |
| [CHANGELOG.md](https://github.com/Jartan-LLC/memcp/blob/main/CHANGELOG.md) | What changed, per release |

## Status

v0.2.0 — API may change before v1.0. Suitable for development and early adoption.

## License

AGPL-3.0 — see [LICENSE](https://github.com/Jartan-LLC/memcp/blob/main/LICENSE).
