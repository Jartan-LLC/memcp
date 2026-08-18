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
| `mem0` | yes | yes, unless you bring a local LLM | mem0's REST server on pgvector. Real embeddings, real extraction, a graph of entities. |
| `cognee` | yes | yes — about $0.50 per 1,000 memory operations, and no keyless substitute this repo has measured is usable ([below](#what-cognee-costs-to-run)) | cognee's server with Kuzu, LanceDB and SQLite inside it. Real embeddings, and the only backend whose `memory_entities` returns relationships as well as entities. |

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
  findable, which is the trade being made. The money is about $0.50 per 1,000 memory
  operations, priced with its arithmetic below.

Two operational differences worth knowing before you pick:

- cognee reports no relevance score, so `search_memory` results carry `score: null`
  and the `threshold` argument does nothing.
- cognee has no per-memory change log, so `memory_history` is not registered.

### What cognee costs to run

**About $0.50 per 1,000 memory operations** at OpenAI list prices — an approximate
figure, and roughly $0.00046 per `add_memory` and $0.00047 per `search_memory`. The
arithmetic is below so you can redo it against your own model choice and today's
prices instead of taking the figure on trust.

Measured against **cognee 1.5.0**, the release `ci/cognee` pins by digest, with every
prompt cognee sent logged at the model endpoint. Input tokens are counted from
cognee's own prompts in `o200k_base`, gpt-4o-mini's encoding. Two runs — 12 writes and
6 writes — agreed.

| Per `add_memory` | Counted |
|---|---|
| chat completions | 2 — one `KnowledgeGraph` extraction (~1,046 input tokens), one `Summary` (~411) |
| chat input | 1,459 tokens |
| embedding calls | ~8, totalling 250 tokens |
| chat output | 71 tokens — the floor, see below |

| Per `search_memory` | Counted |
|---|---|
| chat completions | 1, at 2,329 input tokens |
| embedding calls | 1, at 8 tokens |

That search prompt is a fixed template and does not carry the retrieved memories: 12
memories and 48 memories both produced 2,329.08 input tokens per search, identical to
the token. **Recall cost does not grow with the size of your corpus.**

Prices read **2026-08-18**, for the model pair `memcp up --backend cognee` provisions
— `openai/gpt-4o-mini` at $0.15/M input and $0.60/M output, and
`openai/text-embedding-3-small` at $0.02/M:

- **write** — 1,459 × $0.15/M + 250 × $0.02/M + 400 × $0.60/M = **$0.00046**
- **search** — 2,329 × $0.15/M + 8 × $0.02/M + 200 × $0.60/M = **$0.00047**

**The output-token counts are the estimated part.** The 400 per write and 200 per
search above are estimates; every other number here is counted. The completions in the
measurement came from a deterministic stand-in rather than a language model, so what
it counted is a floor: 71 output tokens per write, and effectively none per search.
Priced at that floor a write is $0.00027 and a search is $0.00035 — the search figure
being its input cost alone. A real extractor writes more than the stand-in, which is
why the estimates above sit above the floor.

Two things this does not price. `docs/local-models.md` counted six to seven
completions per write where this measurement counted two; the gap is `instructor`
retrying a model that cannot satisfy cognee's extraction schema, so a model that
retries costs more than the table says. And a cognee release that changes its pipeline
changes every number above.

**cognee needs an OpenAI-compatible key either way, and there is no keyless cognee
path that is usable on ordinary hardware today.** `memcp up --backend cognee` refuses
to start without `COGNEE_LLM_API_KEY`, and `--llm-base-url` removes the account rather
than the model: measured on five CPU cores with Qwen2.5-1.5B-Instruct Q4_K_M and
`nomic-embed-text-v1.5`, one `add_memory` took three to five minutes, exceeded the
adapter's 120-second default, and sometimes failed outright with cognee's `409` because
the model could not produce its extraction schema — no memory stored, not a worse graph
(`docs/local-models.md`, measured 2026-08-17). On a GPU or a larger model this may look
different, and `tools/local_model_retrieval.py` is the harness to find out.

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

`docs/local-models.md` measures one such endpoint. The short version: on five CPU
cores with a 1.5B model, one `add_memory` through cognee takes three to five minutes,
exceeds the adapter's 120-second default — raise `COGNEE_TIMEOUT` — and sometimes
fails outright because the model cannot produce cognee's extraction schema. Read it
before choosing that path.

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

**If clients get a 421.** The server's own error text is just "Invalid Host header" —
this is what it means. `--external-url` sets `MEMCP_ALLOWED_HOSTS` to the hostname
*you* said the proxy serves under, but some proxies rewrite the Host header to the
upstream address before forwarding it — nginx's `proxy_set_header Host $proxy_host`
does this; Traefik does not. Point that kind of proxy at memcp and every request
arrives with `Host: memcp:8080` instead of `Host: memory.example.com`, and the
deployment refuses all of them, because that is not the name it was told to admit.
Fix it either way:

- Point `--external-url` at what the proxy actually sends (`memcp:8080` in the
  example above), not at the public name. That flag is also what the client snippet
  prints, so this fix is the right one only when the clients are other containers
  reaching memcp at that same address.
- Or add the extra name to `MEMCP_ALLOWED_HOSTS` by hand in the deployment's `.env`,
  alongside the one `--external-url` set. This is the fix that keeps the public
  address printed for clients while admitting the name the proxy sends.

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
