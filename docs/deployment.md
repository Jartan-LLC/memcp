# Deploy memcp

memcp is the MCP server your clients connect to; the memories live in a backend behind
it. You run memcp with Docker Compose, choose a backend, and point your MCP clients at
memcp.

## Choose a backend

| `MEMCP_BACKEND` | What you get | What it needs |
|---|---|---|
| `mem0` | Search by meaning and fact extraction | A [mem0](https://github.com/mem0ai/mem0) server and an LLM endpoint for it |
| `sqlite` | Durable memory with keyword search, nothing else to run | Nothing |
| `in_memory` | Memory that's lost on restart, for tests and demos | Nothing |

### What `sqlite` doesn't do

- **Retrieval is keyword matching.** Two words match when they are equal or share a
  four-character prefix, so `linter` finds `linting`. A question phrased in words that
  don't appear in the memory finds nothing.
- **Nothing is extracted.** `add_memory` stores what it is given. Its `infer` argument
  is accepted and ignored, and `memory_status` reports `extracts_facts: false`.

## Set up with Docker Compose

### Get the files

1. In a new directory, download the compose file and the settings template:

   ```bash
   mkdir memcp && cd memcp &&
     curl -fsSLO https://raw.githubusercontent.com/Jartan-LLC/memcp/main/docker-compose.yml &&
     curl -fsSL -o .env https://raw.githubusercontent.com/Jartan-LLC/memcp/main/.env.example &&
     chmod 600 .env
   ```

2. Generate a token:

   ```bash
   openssl rand -hex 32
   ```

3. In `.env`, set `MEMCP_AUTH_TOKENS=<token>:<your-name>`. `<your-name>` is the tenant
   your memories belong to; give each person or team its own token and name.

Then configure the backend you chose.

### Use mem0

memcp supports mem0's REST server at release v2.2.1, built from mem0's source.

1. Get the server:

   ```bash
   git clone --depth 1 --branch v2.2.1 https://github.com/mem0ai/mem0.git
   ```

2. In `mem0/server`, set it up and start it with mem0's
   [Self-Hosted Setup](https://docs.mem0.ai/open-source/setup) guide. In its `.env`, also
   set `ADMIN_API_KEY` to a long random value (`openssl rand -hex 32`): memcp
   authenticates with it, and deleting all of a scope's memories needs the admin key.
3. mem0's compose file publishes the API (port 8888) and Postgres (port 8432) on every
   interface. Keep clients off both, with a firewall or by removing those `ports:`
   entries once setup is done: memcp enforces tenant isolation, and mem0 doesn't.
4. In the memcp directory, create `docker-compose.override.yml` to join mem0's network:

   ```yaml
   services:
     memcp:
       networks: [default, mem0]
   networks:
     mem0:
       name: mem0-dev_mem0_network
       external: true
   ```

5. In memcp's `.env`, set:

   ```text
   MEMCP_BACKEND=mem0
   MEM0_API_BASE=http://mem0:8000
   MEM0_API_KEY=<mem0's ADMIN_API_KEY>
   ```

### Use sqlite

`.env` already sets `MEMCP_BACKEND=sqlite`. The database lives in a Docker volume and
survives a restart.

### Start memcp

1. Start it:

   ```bash
   docker compose up -d --wait
   ```

2. Add it to your MCP client with your token, as in the
   [README](../README.md#connect-from-claude-code). memcp listens on
   `http://127.0.0.1:8080/mcp`.

## Run a Compose deployment

### Day-to-day tasks

| Task | Command |
|---|---|
| Stop, keeping memories | `docker compose down` |
| Stop and delete memories | `docker compose down --volumes` |
| Replace a token | Edit `MEMCP_AUTH_TOKENS` in `.env`, then `docker compose up -d --wait` |
| Change compose settings | Put them in `docker-compose.override.yml`, which an upgrade doesn't overwrite |

### Upgrade

1. In the memcp directory, download the current compose file:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/Jartan-LLC/memcp/main/docker-compose.yml
   ```

2. List settings the current template has that `.env` lacks, and add the ones you need:

   ```bash
   comm -13 <(grep -o '^[A-Z0-9_]*=' .env | sort) \
     <(curl -fsSL https://raw.githubusercontent.com/Jartan-LLC/memcp/main/.env.example | grep -o '^[A-Z0-9_]*=' | sort)
   ```

3. Pull the latest image and restart:

   ```bash
   docker compose pull && docker compose up -d --wait
   ```

### Pin a release

The compose file runs the latest release. To stay on one, name it in
`docker-compose.override.yml`, using a version from
[Releases](https://github.com/Jartan-LLC/memcp/releases):

```yaml
services:
  memcp:
    image: ghcr.io/jartan-llc/memcp:<version>
```

## Put memcp behind a reverse proxy

When a proxy or platform (Traefik, Caddy, nginx, Dokploy, Coolify) routes to memcp:

1. Set `MEMCP_ALLOWED_HOSTS` in `.env` to the hostname clients use, for example
   `MEMCP_ALLOWED_HOSTS=memory.example.com`. It replaces the compose file's default,
   which admits only `127.0.0.1` and `localhost`.
2. If the proxy runs in Docker, add to `docker-compose.override.yml`, naming the
   proxy's network (`!reset` needs Compose 2.24 or later):

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

### A 421 response

"Invalid Host header" means the Host header the request arrived with isn't in
`MEMCP_ALLOWED_HOSTS`. Some proxies rewrite it to the upstream address (nginx's
`proxy_set_header Host $proxy_host` does; Traefik doesn't), so requests arrive as
`memcp:8080`, or `127.0.0.1:8080` from a proxy on the host. Add that name to the list,
comma-separated.

## Security checklist

- memcp is published on `127.0.0.1` only; anything wider is your change to make.
- The compose file won't start without `MEMCP_AUTH_TOKENS`, and the server refuses to
  run unauthenticated on any interface another machine can reach.
- `.env` holds the tokens: keep it at mode 600 and out of version control.
- Only the loopback Host names are admitted until `MEMCP_ALLOWED_HOSTS` adds yours.
- With mem0, only memcp should reach mem0's API and Postgres.

## Run memcp without Docker

```bash
pipx install memcp-server
MEMCP_BACKEND=sqlite MEMCP_SQLITE_PATH="$HOME/.memcp/memcp.sqlite3" MEMCP_HOST=127.0.0.1 memcp
```

The server listens on `http://localhost:8080`. [reference.md](reference.md) lists every
variable.
