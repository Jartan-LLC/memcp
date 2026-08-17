# Deployment

`memcp up` provisions the memory backend as well as memcp. You do not stand up mem0
or a vector store first and then wire memcp to it — one command creates the whole
stack, mints its credentials, waits for it to be healthy, and prints the MCP client
configuration.

```bash
pipx install memcp-server
memcp up
```

That is the durable, keyless path: one container, one SQLite file, memories that
survive a restart, no account anywhere. It prints a token once and the JSON snippet
to paste into your MCP client.

## Commands

| Command | What it does |
|---|---|
| `memcp plan` | Prints every container, volume, port, environment variable and file the deployment would create. Creates nothing. |
| `memcp up` | Creates it, waits for health, prints the client snippet. Idempotent. |
| `memcp verify` | Stores and retrieves one memory over MCP against a running deployment, and times it. `--url` checks over the address a proxy serves. |
| `memcp status` | What is running. |
| `memcp down` | Stops it. Memories survive. `--volumes` deletes them. |
| `memcp rotate-token` | Mints a new bearer token. Apply it with `memcp up`. |

Everything takes `--dir` (default `.memcp`) and `--project`, so two deployments can
run side by side.

`python -m memcp` with no subcommand still runs the server itself — that is what the
Docker image does and what an existing deployment runs.

## Backends

| `--backend` | Durable | Needs a key | What it is |
|---|---|---|---|
| `sqlite` (default) | yes | no | One SQLite file. Keyword retrieval, no fact extraction, no knowledge graph. |
| `in_memory` | **no** | no | Process memory. Everything is lost on restart. A smoke test, not a deployment. |
| `mem0` | yes | yes, unless you bring a local LLM | mem0's REST server on pgvector. Real embeddings, real extraction, a real graph. |

### The keyless path, and what it costs you

`sqlite` needs no account because it does no LLM work, and that is exactly what it
costs you:

- **Retrieval is keyword matching.** Query and content are split into tokens, and two
  tokens match when they are equal or share a four-character prefix — so `linter`
  finds `linting`, and a question phrased in words that do not appear in the memory
  finds nothing. It is not embedding similarity and it does not know that two
  different words mean the same thing.
- **Nothing is extracted.** `add_memory` stores what you give it. The `infer`
  argument exists on every backend and this one cannot honour it, so it is accepted
  and ignored; `memory_status` reports `extracts_facts: false`.
- **There is no knowledge graph.** `memory_entities` is not registered at all rather
  than answering with an empty one.

For a personal or single-project brain that is usually enough, and it is the
difference between installing memcp and having memory, and installing memcp and then
going to get an OpenAI account. For search that matches on meaning, extraction, and a
graph, you need a model — that is what `mem0` is for.

### mem0 without a provider bill

mem0 embeds and extracts through an OpenAI-compatible client. Point it at a local
endpoint and no provider account is involved:

```bash
# Ollama, llama.cpp, LiteLLM — anything that speaks the OpenAI API
memcp up --backend mem0 --llm-base-url http://192.168.1.10:11434/v1
```

memcp then mints a placeholder for `OPENAI_API_KEY`, because mem0's client library
requires the variable to exist even when the endpoint ignores it. Without
`--llm-base-url`, `OPENAI_API_KEY` is yours to supply and `memcp up` refuses to start
without it rather than standing up a stack that half-works.

**This is plumbing, not a quality claim.** CI exercises the path against a
deterministic stand-in, which proves the stack comes up and the round trip works. How
well any particular local model performs as mem0's extractor or embedder is something
this repository has not measured, so do not read this section as saying memcp works
well with local models — only that it connects to them.

## What gets created

`memcp plan` is the authority, and it is worth reading before the first `up`:

```
$ memcp plan --backend mem0
CONTAINERS
  postgres    pgvector — mem0's vector store and application database
  mem0        mem0's REST server, built from the pinned fork
  memcp       the MCP server your client connects to — the only way in

PUBLISHED PORTS
  127.0.0.1:8080 -> memcp:8080
  no host port, internal network only: mem0, postgres
...
```

`memcp plan --json` is the same thing for a script.

## Behind a platform that routes into the container

Dokploy, Coolify, Kubernetes and a hand-run Traefik or Caddy all own the external
port mapping themselves and route into the container. Under any of them a published
host port is redundant, and a redundant port is a second way in that nothing routes,
watches or terminates TLS on. `--no-publish` provisions the same stack with no host
port at all:

```bash
memcp up \
  --no-publish \
  --network dokploy-network \
  --external-url https://memory.example.com
```

- **`--no-publish`** — the compose file gets no `ports:` key. Nothing on the host can
  reach memcp; only something on its Docker network can.
- **`--network NAME`** — attaches memcp to a network that already exists, as well as
  this deployment's own, so the platform's router can see it. memcp joins that
  network and never creates or deletes it. Leave it off when the router is already on
  the project's default network.
- **`--external-url URL`** — the address clients use. `--no-publish` requires it,
  because `localhost:8080` is not the address any more and memcp cannot derive what
  is. It is what the client snippet prints and what `MEMCP_ALLOWED_HOSTS` admits. If
  the deployment is reached only by other containers, that URL is
  `http://memcp:8080`.

`memcp plan` says the port is absent because you asked, not because the plan forgot:

```
PUBLISHED PORTS
  (none — publishing is off, because you asked for it: --no-publish)
  memcp listens on container port 8080, reachable only from inside Docker,
  over Docker network(s) default, dokploy-network (external).
  Whatever routes to this deployment has to be on one of those networks.

NETWORKS
  default  created by compose for this deployment
  dokploy-network  existing network, joined not created — memcp attaches to it

CLIENTS REACH IT AT
  https://memory.example.com/mcp
```

**What still works, and what changes.** `up --wait` gates on health exactly as before
— the healthcheck runs inside the container and never used the host port. `--smoke`
and `verify` no longer have a host route to take, so they run the same store-and-
retrieve check from inside the container through `docker compose exec`, and say so:
they exercise the MCP protocol, the bearer gate, the adapter and the backend, and
they do **not** exercise your platform's route to it. Once that route is up, check it
with the one command that can:

```bash
memcp verify --url https://memory.example.com/mcp
```

Switching an existing deployment to `--no-publish` (or back) is a normal `up`: same
project, same volumes, same token, same memories.

`.memcp/deployment.json` records how the deployment is reached — published port,
container port, client URL, networks. It holds no credential, and it is how a later
`verify` knows which route exists.

## How it is secured

- **memcp is the only service with a host port, and it binds `127.0.0.1`.** The
  engine and its datastore have no published port at all. memcp enforces tenant
  isolation in-process, so a route to the engine that skips memcp has no isolation —
  mem0's own compose file publishes both its API and its Postgres, and adopting it as
  written would stand an unauthenticated store beside the gate. `--bind 0.0.0.0` is
  available and is a decision you type. `--no-publish` goes the other way and is
  strictly narrower: no service in the stack has a host port at all.
- **Every credential is minted**, including the memcp↔mem0 link on the private
  network, and the pgvector password. Nothing in this repository ships a fixed
  password. The `.env` holding them is created at mode 0600 and the deployment
  directory carries a `.gitignore` that makes it uncommittable.
- **The token prints once.** It is not in the compose file, not in `memcp plan`, and
  not in the logs. Lost it? `memcp rotate-token`, then `memcp up`.
- **Images are pinned by digest.** Moving a pin is a commit in
  `memcp/deploy/images.py`.
- **`memcp up` is a command you run, never a capability the server holds.** Nothing
  generated mounts the Docker socket, and the memcp container has no path to the
  Docker daemon.
- **Unauthenticated on a public interface is refused.** memcp resolves tenant
  identity from the bearer token, so no token means one tenant and no gate. On
  loopback that is a dev server; on any interface another machine can reach, the
  server refuses to start and names both ways out.

### Host header validation

memcp passes `MEMCP_ALLOWED_HOSTS` to the MCP SDK's DNS-rebinding protection.
Provisioned deployments set it to the loopback names plus whatever `--bind` says.

`--external-url` adds the hostname a proxy serves under, so a provisioned deployment
behind one is not a deployment you have to remember to configure afterwards. The
loopback names stay in the list either way — the container's own healthcheck and the
in-container first-memory check both go through `localhost`.

Running the server yourself, the SDK's own rule applies when the variable is unset:
protection is enabled only when `MEMCP_HOST` is `127.0.0.1`, `localhost` or `::1`,
and **is off for every other value including `0.0.0.0`**, which is memcp's default
bind. Behind a reverse proxy, set `MEMCP_ALLOWED_HOSTS` to the hostname you serve.

## Re-running, and taking it down

`memcp up` against an existing deployment reconciles it. It reuses the credentials
already on disk, does not rotate the token, and does not touch the volumes. `memcp
down` stops the containers and leaves the volumes; `memcp down --volumes` deletes
them, and every memory with them.

## Where the memcp image comes from

`--memcp-source` decides:

- `auto` (default) — the checkout you are in, if you are in one; otherwise PyPI.
- `pypi` — generates a Dockerfile that installs `memcp-server` at this version onto
  a digest-pinned Python base. This is what a `pipx install` gets.
- a path — build from that memcp checkout.

## Running the server without provisioning

`.env.example` covers every variable, and `docker-compose.yml` at the repository root
is a single-service compose file for someone managing the stack by hand.
