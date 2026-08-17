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
| `memcp verify` | Stores and retrieves one memory over MCP against a running deployment, and times it. |
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
| `mem0` | yes | yes, unless you bring a local LLM | mem0's REST server on pgvector. Real embeddings, real extraction, a graph of entities. |
| `cognee` | yes | yes, unless you bring a local LLM | cognee's server with Kuzu, LanceDB and SQLite inside it. Real embeddings, and the only backend whose `memory_entities` returns relationships as well as entities. |

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
graph, you need a model — that is what `mem0` and `cognee` are for.

### Choosing between mem0 and cognee

Both need a model and both retrieve semantically. They differ in what they keep.

- **`mem0` decides what is worth storing.** `add_memory` with `infer=true` runs your
  text through an LLM and may store a distilled fact, several, or nothing at all.
  `memory_entities` returns entities, without the relationships between them.
- **`cognee` keeps your text and builds a graph beside it.** Content is stored
  verbatim whatever `infer` says, and the extraction pass produces entities *and* the
  edges between them, which is what `memory_entities` returns. The cost is speed and
  money: every write runs extraction and embedding inside the request, so `add_memory`
  is slower here than on any other backend — and it returns only once the memory is
  findable, which is the trade being made.

Two operational differences worth knowing before you pick:

- cognee reports no relevance score, so `search_memory` results carry `score: null`
  and the `threshold` argument does nothing.
- cognee has no per-memory change log, so `memory_history` is not registered.

### cognee, and the tenant boundary it depends on

memcp derives one cognee account per tenant — the login from a hash of the tenant id,
the password from `COGNEE_TENANT_SECRET` — and cognee's own per-user access control is
what keeps those accounts apart. That is not a detail: a cognee started without
`ENABLE_BACKEND_ACCESS_CONTROL=true` serves every request as one default user, and
every memcp tenant would read and write the same memories.

`memcp up --backend cognee` sets it. Pointed at a cognee you configured yourself,
memcp checks: `/health` asks cognee whether it refuses an unauthenticated read, and
reports **unhealthy** if it does not. Since `memcp up --wait` gates on that, a
misconfigured server fails provisioning rather than quietly collapsing your tenants.

`COGNEE_TENANT_SECRET` is minted per deployment and lives only in the deployment's
`.env`. Losing it does not lose the memories, but a different value derives different
accounts, so every tenant would come up empty.

### mem0 or cognee without a provider bill

Both embed and extract through an OpenAI-compatible client. Point either at a local
endpoint and no provider account is involved:

```bash
# Ollama, llama.cpp, LiteLLM — anything that speaks the OpenAI API
memcp up --backend mem0   --llm-base-url http://192.168.1.10:11434/v1
memcp up --backend cognee --llm-base-url http://192.168.1.10:11434/v1
```

memcp then mints a placeholder for the provider key, because both client libraries
require the variable to exist even when the endpoint ignores it. Without
`--llm-base-url`, that key is yours to supply — `OPENAI_API_KEY` for mem0,
`COGNEE_LLM_API_KEY` for cognee — and `memcp up` refuses to start without it rather
than standing up a stack that half-works.

**This is plumbing, not a quality claim.** CI exercises the path against a
deterministic stand-in, which proves the stack comes up and the round trip works. How
well any particular local model performs as an extractor or an embedder is something
this repository has not measured, so do not read this section as saying memcp works
well with local models — only that it connects to them. That gap is larger for cognee
than for mem0: cognee's graph is only as good as the entities the model finds, and a
small local model finding few or wrong entities would produce a graph that looks
populated and is not worth querying.

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

## How it is secured

- **memcp is the only service with a host port, and it binds `127.0.0.1`.** The
  engine and its datastore have no published port at all. memcp enforces tenant
  isolation in-process, so a route to the engine that skips memcp has no isolation —
  mem0's own compose file publishes both its API and its Postgres, and adopting it as
  written would stand an unauthenticated store beside the gate. `--bind 0.0.0.0` is
  available and is a decision you type.
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
