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

### Get memcp's compose files

1. In a new directory, download the compose file and the settings template:

   ```bash
   mkdir memcp && cd memcp &&
     curl -fsSLO https://raw.githubusercontent.com/Jartan-LLC/memcp/main/docker-compose.yml &&
     curl -fsSL -o .env https://raw.githubusercontent.com/Jartan-LLC/memcp/main/.env.example &&
     chmod 600 .env
   ```

2. Generate a token, and write `keys.toml` with a key that holds `writer` on a space,
   both named after your login. A key name holds only letters, digits, `_`, `.` and `-`:
   if your login has anything else, or `USER` is unset, replace `$USER` with such a name.

   ```bash
   TOKEN=$(openssl rand -hex 32) && echo "$TOKEN" &&
     printf '[[keys]]\nname = "%s"\nsecrets = ["sha256:%s"]\ngrants = [{ space = "%s", relation = "writer" }]\n' \
       "$USER" "$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)" "$USER" > keys.toml
   ```

   Keep the token that printed: your clients send it, and `keys.toml` holds only its
   hash. On macOS, use `shasum -a 256` in place of `sha256sum`.

Then configure the backend you chose.

### Use mem0

memcp supports mem0's REST server at release v2.2.1, built from mem0's source. Setting
it up needs `git`, `make` and `python3`.

1. From the memcp directory, get the server beside it and go to its `server/` folder:

   ```bash
   cd .. && git clone --depth 1 --branch v2.2.1 https://github.com/mem0ai/mem0.git &&
     cd mem0/server
   ```

2. Create `.env` as in the "Configure the environment" step of mem0's
   [Self-Hosted Setup](https://docs.mem0.ai/open-source/setup) guide. Also set
   `POSTGRES_PASSWORD`, which mem0's compose file requires, and `ADMIN_API_KEY`, which
   memcp authenticates with, each to a long random value (`openssl rand -hex 32`).
3. Create `docker-compose.override.yaml`, which keeps mem0's API and dashboard on this
   machine, drops Postgres's host port and restarts the services after a reboot
   (`!override` and `!reset` need Compose 2.24 or later):

   ```yaml
   services:
     mem0:
       ports: !override
         - "127.0.0.1:8888:8000"
       restart: unless-stopped
     mem0-dashboard:
       ports: !override
         - "127.0.0.1:3000:3000"
       restart: unless-stopped
     postgres:
       ports: !reset []
       restart: unless-stopped
   ```

4. Start mem0 and create its admin account, and keep the admin password it prints:

   ```bash
   make bootstrap
   ```

5. Back in the memcp directory (`cd ../../memcp`), create `docker-compose.override.yml`
   to join mem0's network:

   ```yaml
   services:
     memcp:
       networks: [default, mem0]
   networks:
     mem0:
       name: mem0-dev_mem0_network
       external: true
   ```

6. In memcp's `.env`, set:

   ```text
   MEMCP_BACKEND=mem0
   MEM0_API_BASE=http://mem0:8000
   MEM0_API_KEY=<mem0's ADMIN_API_KEY>
   ```

### Use sqlite

`.env` already sets `MEMCP_BACKEND=sqlite`. The database lives in a Docker volume and
survives a restart.

### Start memcp and connect a client

1. Start it:

   ```bash
   docker compose up -d --wait
   ```

2. Add it to your MCP client with your token, as in the
   [README](../README.md#connect-from-claude-code). memcp listens on
   `http://127.0.0.1:8080/mcp`.

## Run a Compose deployment

### Day-to-day Compose tasks

| Task | Command |
|---|---|
| Stop, keeping memories | `docker compose down` |
| Stop and delete memories | `docker compose down --volumes`; with mem0, the memories are in mem0's database: `make clean` in `mem0/server` deletes them, and mem0's admin account with them |
| Add, change or remove a key | Edit `keys.toml` ([Manage keys](#manage-keys)), then `docker compose up -d --wait --force-recreate memcp` |
| Change compose settings | Put them in `docker-compose.override.yml`, which an upgrade doesn't overwrite. Keep one `memcp` entry there: merge each snippet into it, list every network in its `networks:`, and declare each network once under the top-level `networks:` |

### Upgrade a Compose deployment

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

### Upgrade from 0.1.x

`keys.toml` replaces 0.1.x's `MEMCP_AUTH_TOKENS`, and memcp refuses to start while that
is set.

1. Stop the 0.1.x deployment with its own compose file, before replacing that file:

   ```bash
   docker compose down
   ```

2. Download the current compose file, as in
   [Upgrade a Compose deployment](#upgrade-a-compose-deployment).
3. For each `<token>:<user_id>` in `MEMCP_AUTH_TOKENS`, add a key to `keys.toml` with its
   own name, `writer` on the `user_id` as its space, and the token's hash as its secret,
   made with the "token you already have" command in [Manage keys](#manage-keys). A client
   keeps its token only if it is random and as long as `openssl rand -hex 32` makes; give
   any other client a new one, since a short or guessable token can be recovered from its
   hash. If `MEMCP_AUTH_TOKENS` was empty, the memories are in the space `default_user`:
   add one key with `writer` on it and a new token's hash.
4. Delete the `MEMCP_AUTH_TOKENS` line from `.env`.
5. Pull and start:

   ```bash
   docker compose pull && docker compose up -d --wait
   ```

6. Give each client that got a new token its token, as in
   [Start memcp and connect a client](#start-memcp-and-connect-a-client).

### Pin a release

The compose file runs the latest release. To stay on one, name it in
`docker-compose.override.yml`, using a version from
[Releases](https://github.com/Jartan-LLC/memcp/releases) without its leading `v` (for
example `0.2.0`):

```yaml
services:
  memcp:
    image: ghcr.io/jartan-llc/memcp:<version>
```

## Manage keys

Each key in `keys.toml` has a name, the hashes of its tokens and one grant: a space and
a relation, `reader` or `writer`. [reference.md](reference.md#keys-file) has every rule.

```toml
[[keys]]
name = "laptop"
secrets = ["sha256:<64 hex digits>"]
grants = [{ space = "notes", relation = "writer" }]
```

Generate a token and print its hash for `secrets`:

```bash
TOKEN=$(openssl rand -hex 32) && echo "$TOKEN" &&
  printf 'sha256:%s\n' "$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)"
```

Or hash a token you already have: paste it after running this, then press Enter.

```bash
read -rs TOKEN && printf 'sha256:%s\n' "$(printf %s "$TOKEN" | sha256sum | cut -d' ' -f1)"
```

| Task | Change in `keys.toml` |
|---|---|
| Add a client | Add a `[[keys]]` block with a new name and a new token's hash |
| Let a client only read | Set its grant's `relation` to `reader` |
| Share a space | Grant the same `space` to each client's key |
| Rotate a token | Add the new token's hash to the key's `secrets`, move clients to the new token, then remove the old hash |
| Revoke a client | Remove its hash; remove the whole key if that is its only one |

Apply each change with `docker compose up -d --wait --force-recreate memcp`, which fails
if memcp refuses the file. Don't give a removed
key's name to a new key: memories record the name of the key that wrote them. memcp reads
the file as its own user, so leave it readable (mode 644). It holds hashes, not tokens:
a hash keeps a random token secret, but not a guessable one.

## Put memcp behind a reverse proxy

When a proxy or platform (Traefik, Caddy, nginx, Dokploy, Coolify) routes to memcp:

1. Set `MEMCP_ALLOWED_HOSTS` in `.env` to the hostname clients use, for example
   `MEMCP_ALLOWED_HOSTS=memory.example.com`. It replaces the compose file's default,
   which admits only `127.0.0.1` and `localhost`.
2. If the proxy runs in Docker, add to `docker-compose.override.yml`, naming the
   proxy's network (`!reset` needs Compose 2.24 or later), merged with anything already
   there:

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
- The compose file won't start without `keys.toml`, and the server refuses to run
  unauthenticated on any interface another machine can reach.
- Give each client its own key, with `reader` where it only reads.
- `keys.toml` holds only token hashes; version-control it only while every token it
  hashes is random (`openssl rand -hex 32`).
- `.env` holds the mem0 API key: keep it at mode 600 and out of version control.
- Only the `127.0.0.1` and `localhost` Host names are admitted unless
  `MEMCP_ALLOWED_HOSTS` is set; a value replaces them.
- With mem0, only memcp should reach mem0's API, dashboard and Postgres. A host firewall
  such as ufw doesn't filter Docker's published ports; mem0's override keeps them on
  loopback.
- With mem0, its admin account exists (`make bootstrap`), so nobody else can sign up as
  mem0's admin.

## Run memcp without Docker

```bash
pipx install memcp-server
MEMCP_BACKEND=sqlite MEMCP_SQLITE_PATH="$HOME/.memcp/memcp.sqlite3" MEMCP_HOST=127.0.0.1 memcp
```

Clients connect to `http://127.0.0.1:8080/mcp` with no token. To require tokens, set
`MEMCP_KEYS_FILE` to a [keys file](#manage-keys). [reference.md](reference.md) lists every
variable.
