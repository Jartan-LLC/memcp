# memcp

[![PyPI](https://img.shields.io/pypi/v/memcp-server)](https://pypi.org/project/memcp-server/)
[![CI](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Jartan-LLC/memcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/Jartan-LLC/memcp/blob/main/LICENSE)

Backend-agnostic, multi-tenant MCP memory server. AI clients connect and get
persistent long-term memory over streamable HTTP.

One command provisions the memory backend too — you do not stand up a memory engine
first and then wire memcp to it.

## Quickstart

```bash
pipx install memcp-server
memcp up
```

Needs Docker and nothing else. It creates the stack, waits until it is healthy,
prints a bearer token once, and prints the MCP client snippet containing it.

### Time to first memory

| Backend | Up and healthy | Then store + retrieve one memory | Total |
|---|---|---|---|
| `sqlite` (default) | 18.3–19.1s | 0.11s | **18.4s, 18.5s, 19.2s** across three runs |
| `mem0` | 54–56s | 2.4–3.1s | 56–59s |

Measured from `memcp up` starting to `add_memory` then `search_memory` both
succeeding over MCP, on a clean GitHub-hosted runner with an empty image cache.
Nearly all of it is building and starting containers: `mem0` is slower because it
builds mem0 from source and starts pgvector beside it. The `provision` job runs this
on every pull request and publishes `TIME_TO_FIRST_MEMORY_SECONDS` to its summary, so
these are numbers this repository ran rather than ones it estimated.

**No account, no API key.** The default backend is `sqlite`. Memories go to a file on
disk and survive a restart. Search is keyword matching, so it finds a memory when your
question reuses its words and misses one phrased differently, and it stores what you
give it word for word instead of pulling facts out of it. Semantic search and the
knowledge graph need mem0 and a key. A client does not have to guess which it is
talking to: `memory_status` says.

```bash
memcp plan          # everything it will create, before it creates any of it
memcp up --smoke    # create it, then store and retrieve one memory over MCP
memcp down          # stop it, keep the memories
```

## Connect from Claude Code

Add to your MCP settings (Claude Code → Settings → MCP Servers):

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

For an authenticated deployment, add the token `memcp up` printed:

```json
{
  "mcpServers": {
    "memcp": {
      "type": "streamable-http",
      "url": "https://your-host/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

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

- One-command deployment that provisions memcp **and** its memory backend
- Durable, keyless local storage (`sqlite`), or mem0 on pgvector for semantic search and a knowledge graph
- Add, search, list, update and delete memories over MCP — 12 tools, frozen surface
- Flat scope-based filtering (`agent_id`, `run_id`)
- Per-tenant bearer token auth, minted per deployment, never defaulted
- Stateless HTTP transport — safe behind reverse proxies

## Documentation

| Document | What it covers |
|---|---|
| [deployment.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/deployment.md) | `memcp up` in full — backends, the mem0 stack, credentials, running behind a platform that routes into the container, and running the server without provisioning at all |
| [reference.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/reference.md) | Environment variables, the 12 MCP tools, and known limitations per backend |
| [conformance.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/conformance.md) | Holding a `MemoryBackend` to the suite, including one in another repository |
| [development.md](https://github.com/Jartan-LLC/memcp/blob/main/docs/development.md) | Working on memcp itself — install, the check loop, the conformance run |
| [CHANGELOG.md](https://github.com/Jartan-LLC/memcp/blob/main/CHANGELOG.md) | What changed, per release |

## Status

v0.2.0 — API may change before v1.0. Suitable for development and early adoption.

## License

AGPL-3.0 — see [LICENSE](https://github.com/Jartan-LLC/memcp/blob/main/LICENSE).
