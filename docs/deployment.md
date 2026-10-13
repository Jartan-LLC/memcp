# Deployment

memcp ships as a Docker image, `ghcr.io/jartan-llc/memcp`, and as the `memcp-server`
package on PyPI. The repository's `docker-compose.yml` runs the image with the `sqlite`
backend: one container, one database file, no API key.

## Run it with Docker Compose

1. Download the compose file and the environment template:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/Jartan-LLC/memcp/main/docker-compose.yml
   curl -fsSL -o .env https://raw.githubusercontent.com/Jartan-LLC/memcp/main/.env.example
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
| Pin a release | Set `MEMCP_VERSION=<version>` in `.env` (default: `latest`) |
| Upgrade | `docker compose pull && docker compose up -d --wait` |
| Stop, keeping memories | `docker compose down` |
| Stop and delete memories | `docker compose down --volumes` |
| Replace a token | Edit `MEMCP_AUTH_TOKENS` in `.env`, then `docker compose up -d --wait` |

## Backends

| `MEMCP_BACKEND` | Durable | Needs a key | What it is |
|---|---|---|---|
| `sqlite` (compose default) | yes | no | One SQLite file. Keyword retrieval, no fact extraction, no knowledge graph. |
| `in_memory` | **no** | no | Process memory, lost on restart. For tests and demos. |
| `mem0` | yes | for mem0's LLM | A [mem0](https://github.com/mem0ai/mem0) REST server you run. Embedding search, fact extraction, a knowledge graph. |

To use mem0, set `MEMCP_BACKEND=mem0`, `MEM0_API_BASE` and `MEM0_API_KEY` in `.env`.
memcp enforces tenant isolation itself, so keep mem0 and its database off any network a
client can reach: only memcp should be.

### What `sqlite` doesn't do

- **Retrieval is keyword matching.** Two words match when they are equal or share a
  four-character prefix, so `linter` finds `linting`. A question phrased in words that
  don't appear in the memory finds nothing.
- **Nothing is extracted.** `add_memory` stores what it is given. Its `infer` argument
  is accepted and ignored, and `memory_status` reports `extracts_facts: false`.
- **There is no knowledge graph.** `memory_entities` isn't registered.

For search by meaning, extraction and a graph, use mem0.

## Behind a reverse proxy

When a proxy or platform (Traefik, Caddy, nginx, Dokploy, Coolify, Kubernetes) routes to
memcp:

1. Set `MEMCP_ALLOWED_HOSTS` in `.env` to the hostname clients use, for example
   `MEMCP_ALLOWED_HOSTS=memory.example.com`. It replaces the compose file's default,
   which admits only `127.0.0.1` and `localhost`.
2. If the proxy runs in Docker, create `docker-compose.override.yml` beside
   `docker-compose.yml`, naming the proxy's network:

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
4. Point the proxy at `memcp:8080`, and clients at `https://<hostname>/mcp`.

**A 421 response ("Invalid Host header")** means the Host header the request arrived
with isn't in `MEMCP_ALLOWED_HOSTS`. Some proxies rewrite it to the upstream address
(nginx's `proxy_set_header Host $proxy_host` does; Traefik doesn't), so requests arrive
as `memcp:8080`. Add that name to the list, comma-separated.

## How it is secured

- **Loopback only by default.** The compose file publishes memcp on `127.0.0.1`.
  Changing that is a decision you make in the file.
- **No token, no public interface.** Without `MEMCP_AUTH_TOKENS` every request is served
  as one tenant, so memcp refuses to start on any interface another machine can reach.
- **Tokens stay in `.env`.** The compose file reads them from there; the repository's
  `.gitignore` excludes `.env`.
- **Host headers are checked.** The compose file admits only the loopback names unless
  `MEMCP_ALLOWED_HOSTS` says otherwise.
- **Images are built once per release tag**, from a digest-pinned Python base.

## Running without Docker

```bash
pip install memcp-server
MEMCP_BACKEND=sqlite MEMCP_HOST=127.0.0.1 python -m memcp
```

The server listens on `http://localhost:8080`. [reference.md](reference.md) lists every
variable.

## Running a checkout

From a clone, `docker-compose.build.yml` builds the image from the working tree instead
of pulling a release:

```bash
cp .env.example .env    # then set MEMCP_AUTH_TOKENS
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait
```
