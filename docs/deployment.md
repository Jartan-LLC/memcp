# Deployment

memcp ships as a Docker image, `ghcr.io/jartan-llc/memcp`, and as the `memcp-server`
package on PyPI. Each release's `docker-compose.yml` runs that release's image with the
`sqlite` backend: one container, one database file, no API key.

## Run memcp with Docker Compose

1. In a new directory, download the latest release's compose file and environment
   template:

   ```bash
   mkdir memcp && cd memcp &&
     curl -fsSLO https://github.com/Jartan-LLC/memcp/releases/latest/download/docker-compose.yml &&
     curl -fsSL -o .env https://github.com/Jartan-LLC/memcp/releases/latest/download/env.example &&
     chmod 600 .env
   ```

2. Generate a token:

   ```bash
   openssl rand -hex 32
   ```

3. In `.env`, set `MEMCP_AUTH_TOKENS=<token>:<your-name>`. `<your-name>` is the tenant
   your memories belong to; give each person or team its own token and name.
4. Start it:

   ```bash
   docker compose up -d --wait
   ```

5. Add the server to your MCP client with the token, as in the
   [README](../README.md#connect-from-claude-code).

memcp listens on `127.0.0.1:8080`. Memories live in a Docker volume and survive a
restart.

| Task | Command |
|---|---|
| Run a different release | Set `MEMCP_VERSION=<version>` in `.env` (0.2.0 or later), then `docker compose up -d --wait` |
| Change the compose settings | Put the changes in `docker-compose.override.yml`, which an upgrade doesn't overwrite |
| Stop, keeping memories | `docker compose down` |
| Stop and delete memories | `docker compose down --volumes` |
| Replace a token | Edit `MEMCP_AUTH_TOKENS` in `.env`, then `docker compose up -d --wait` |

## Upgrade a Compose deployment

1. In the memcp directory, download the new compose file:

   ```bash
   curl -fsSLO https://github.com/Jartan-LLC/memcp/releases/latest/download/docker-compose.yml
   ```

2. List settings the new template has that `.env` lacks, and add the ones you need:

   ```bash
   comm -13 <(grep -o '^[A-Z0-9_]*=' .env | sort) \
     <(curl -fsSL https://github.com/Jartan-LLC/memcp/releases/latest/download/env.example | grep -o '^[A-Z0-9_]*=' | sort)
   ```

3. If `.env` sets `MEMCP_VERSION`, change it to the new version (the release's tag
   without the `v`, from [Releases](https://github.com/Jartan-LLC/memcp/releases)) or
   remove it.
4. Restart:

   ```bash
   docker compose up -d --wait
   ```

## Backends

| `MEMCP_BACKEND` | Durable | Needs a key | What it is |
|---|---|---|---|
| `sqlite` (compose default) | yes | no | One SQLite file. Keyword retrieval, no fact extraction, no knowledge graph. |
| `in_memory` | **no** | no | Process memory, lost on restart. For tests and demos. |
| `mem0` | yes | for mem0's LLM | A [mem0](https://github.com/mem0ai/mem0) REST server you run. Embedding search, fact extraction, a knowledge graph. |

To use mem0:

1. Run mem0 in Docker, on a network that only mem0, its own services and memcp join.
2. Create `docker-compose.override.yml` beside `docker-compose.yml`, naming mem0's
   network:

   ```yaml
   services:
     memcp:
       networks: [default, mem0]
   networks:
     mem0:
       name: <mem0-network>
       external: true
   ```

3. In `.env`, set `MEMCP_BACKEND=mem0`, `MEM0_API_KEY`, and `MEM0_API_BASE` to mem0's
   address on that network, such as `http://<mem0-container>:<port>` (memcp resolves it
   from inside its container, where `localhost` is memcp itself).
4. Apply it with `docker compose up -d --wait`.

### What `sqlite` doesn't do

- **Retrieval is keyword matching.** Two words match when they are equal or share a
  four-character prefix, so `linter` finds `linting`. A question phrased in words that
  don't appear in the memory finds nothing.
- **Nothing is extracted.** `add_memory` stores what it is given. Its `infer` argument
  is accepted and ignored, and `memory_status` reports `extracts_facts: false`.
- **There is no knowledge graph.** `memory_entities` isn't registered.

For search by meaning, extraction and a graph, use mem0.

## Behind a reverse proxy

When a proxy or platform (Traefik, Caddy, nginx, Dokploy, Coolify) routes to memcp:

1. Set `MEMCP_ALLOWED_HOSTS` in `.env` to the hostname clients use, for example
   `MEMCP_ALLOWED_HOSTS=memory.example.com`. It replaces the compose file's default,
   which admits only `127.0.0.1` and `localhost`.
2. If the proxy runs in Docker, create `docker-compose.override.yml` beside
   `docker-compose.yml`, naming the proxy's network (`!reset` needs Compose 2.24 or
   later):

   ```yaml
   services:
     memcp:
       ports: !reset []
       networks: [default, proxy]
   networks:
     proxy:
       name: <proxy-network>
       external: true
   ```

3. Apply it with `docker compose up -d --wait`.
4. Point the proxy at memcp (`memcp:8080` from the proxy's Docker network,
   `127.0.0.1:8080` from the host), and clients at `https://<hostname>/mcp`.

**A 421 response ("Invalid Host header")** means the Host header the request arrived
with isn't in `MEMCP_ALLOWED_HOSTS`. Some proxies rewrite it to the upstream address
(nginx's `proxy_set_header Host $proxy_host` does; Traefik doesn't), so requests arrive
as `memcp:8080`, or `127.0.0.1:8080` from a proxy on the host. Add that name to the
list, comma-separated.

## How a Compose deployment is secured

- **Loopback only by default.** The compose file publishes memcp on `127.0.0.1`.
- **A token is required.** The compose file won't start without `MEMCP_AUTH_TOKENS`, and
  the server refuses to run unauthenticated on any interface another machine can reach.
- **Tokens live in `.env`.** Keep it out of version control.
- **Host headers are checked.** Only the loopback names are admitted unless
  `MEMCP_ALLOWED_HOSTS` says otherwise.

## Run memcp without Docker

```bash
pipx install memcp-server
MEMCP_BACKEND=sqlite MEMCP_SQLITE_PATH="$HOME/.memcp/memcp.sqlite3" MEMCP_HOST=127.0.0.1 memcp
```

The server listens on `http://localhost:8080`. [reference.md](reference.md) lists every
variable.

## Run memcp from a checkout

From a clone, `docker-compose.build.yml` builds the image from the working tree instead
of pulling a release. It runs as its own Compose project, `memcp-checkout`, so it leaves a
release deployment's container and memories alone, but it uses the same port.

1. Stop any running release deployment.
2. Create `.env`:

   ```bash
   cp .env.example .env && chmod 600 .env
   ```

3. In `.env`, set `MEMCP_AUTH_TOKENS=<token>:<your-name>` with a token from
   `openssl rand -hex 32`.
4. Build and start it:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait
   ```
