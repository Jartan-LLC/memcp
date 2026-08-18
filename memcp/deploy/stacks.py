"""The stacks `memcp up` can provision, one function per backend.

Each returns a `Deployment` — a full declaration of what will be created, which is
what `memcp plan` prints and what the compose file is rendered from. Nothing about
a stack is expressed anywhere else.

Two rules hold across all of them, and they are the ones the security gate turns on:

- **memcp is the only service with a host port, and it binds loopback by default.**
  Engine and datastore sit on the compose network with no published port, so the only
  route to stored memories is through memcp's bearer gate. mem0's own compose file
  publishes both its API and its Postgres; adopting it as written would stand an
  unauthenticated store beside the gate rather than behind it (G2).
- **Every credential is minted.** Including the memcp↔engine link, which is
  authenticated even though it never leaves the compose network (G3).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from memcp.deploy.images import COGNEE, MEM0_SOURCE_PIN, MEM0_SOURCE_REPO, PGVECTOR
from memcp.deploy.model import (
    BindMount,
    Deployment,
    EnvVar,
    Network,
    Port,
    RequiredSecret,
    Service,
    VolumeMount,
)

BACKENDS = ("sqlite", "in_memory", "mem0", "cognee")
DEFAULT_BACKEND = "sqlite"
DEFAULT_PROJECT = "memcp"
DEFAULT_PORT = 8080
CONTAINER_PORT = 8080
LOOPBACK = "127.0.0.1"
# The compose network every service joins unless told otherwise. memcp has to name it
# explicitly once it also joins an external one: a service that declares any network
# stops joining the default implicitly, and losing it would cut memcp off from mem0.
DEFAULT_NETWORK = "default"

# The tenant the minted token resolves to. One deployment, one owner — a second
# identity is a second token, added to MEMCP_AUTH_TOKENS by hand.
DEFAULT_TENANT = "owner"

SQLITE_DATA_PATH = "/data/memcp.sqlite3"

MEMCP_TOKEN_VAR = "MEMCP_TOKEN"
MEM0_ADMIN_KEY_VAR = "MEM0_ADMIN_API_KEY"
MEM0_JWT_SECRET_VAR = "MEM0_JWT_SECRET"
POSTGRES_PASSWORD_VAR = "POSTGRES_PASSWORD"
OPENAI_API_KEY_VAR = "OPENAI_API_KEY"
COGNEE_TENANT_SECRET_VAR = "COGNEE_TENANT_SECRET"
COGNEE_LLM_API_KEY_VAR = "COGNEE_LLM_API_KEY"


@dataclass(frozen=True)
class StackOptions:
    """Everything a stack needs that is not the backend's own shape."""

    build_context: str
    build_dockerfile: str | None = None
    port: int = DEFAULT_PORT
    bind: str = LOOPBACK
    project: str = DEFAULT_PROJECT
    directory: str = ""
    generates_dockerfile: bool = False
    # Whether memcp publishes a host port at all. It does by default and that does not
    # change; False is for a platform that routes into the container itself — Dokploy,
    # Coolify, Kubernetes, a hand-run Traefik — where a published port is redundant at
    # best and a second, unrouted way in at worst.
    publish: bool = True
    # An existing Docker network to attach memcp to, when the platform's router is not
    # on this deployment's own project network.
    network: str | None = None
    # The URL clients actually reach this deployment at, when that is not
    # `http://<bind>:<port>`. It is what the client snippet prints and what Host
    # validation admits.
    external_url: str | None = None
    # An OpenAI-compatible endpoint for mem0's LLM and embedder — Ollama, llama.cpp,
    # LiteLLM, or anything else that speaks the API. Set it and the provider key
    # stops being something the operator has to hold.
    llm_base_url: str | None = None


MEMCP_TOKEN_SECRET = RequiredSecret(
    MEMCP_TOKEN_VAR,
    minted=True,
    description=(
        "The bearer token MCP clients send to this deployment. Printed once at first "
        "`up`; replace it with `memcp rotate-token`."
    ),
)


def _memcp_healthcheck() -> dict[str, object]:
    """memcp's own /health, which calls through to the backend.

    This is the deployment's real readiness signal, not a liveness probe: for the
    mem0 stack it performs an authenticated request against mem0, so memcp reporting
    healthy means the whole chain — gate, adapter, credential, datastore — works.
    `up --wait` gates on it (C1), which is also what closes GitHub #24: the engine's
    own probe is allowed to be shallow because it is not what anything trusts.
    """
    return {
        "test": [
            "CMD",
            "python",
            "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen("
            "'http://localhost:8080/health',timeout=5).status==200 else 1)",
        ],
        "interval": "5s",
        "timeout": "10s",
        "retries": 20,
        "start_period": "10s",
    }


def _llm_secret(options: StackOptions) -> RequiredSecret:
    """Who supplies mem0's provider key — the operator, or nobody.

    mem0 embeds and extracts through an OpenAI-compatible client, and that client
    demands a key string whether or not the endpoint checks it. Pointed at a local
    endpoint the operator named, memcp mints a placeholder: it is satisfying a client
    library, not inventing access to a paid account. Pointed at OpenAI itself, the
    key is real money and memcp refuses to guess (C3).
    """
    if options.llm_base_url:
        return RequiredSecret(
            OPENAI_API_KEY_VAR,
            minted=True,
            description=(
                f"placeholder for the local endpoint at {options.llm_base_url}, which "
                "does not check it — mem0's client library requires the variable to exist"
            ),
        )
    return RequiredSecret(
        OPENAI_API_KEY_VAR,
        minted=False,
        description=(
            "mem0 embeds and extracts through an LLM provider and cannot start "
            "without one. memcp will not invent it."
        ),
        how_to_obtain=(
            "Create a key at https://platform.openai.com/api-keys, then "
            f"export {OPENAI_API_KEY_VAR}=sk-... — it is a metered account, so this "
            "deployment costs money per memory written. To keep the money out of it, "
            "point --llm-base-url at a local OpenAI-compatible endpoint, or use "
            "--backend sqlite, which needs no LLM at all."
        ),
    )


def _format_host(hostname: str, port: int | None) -> str:
    """A hostname and optional port, as the SDK's Host matcher expects it.

    An IPv6 literal contains colons of its own, so it is bracketed the way the
    hardcoded `[::1]` entry already is — otherwise it is indistinguishable from a
    trailing `:port`.
    """
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{host}:{port}" if port else host


def external_host(url: str) -> str | None:
    """The Host header a client sends when it reaches this deployment at `url`."""
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    return _format_host(parts.hostname, parts.port)


def _raw_external_host(url: str) -> str | None:
    """`external_host`, but in the case the operator typed it.

    `urlsplit().hostname` lowercases; the SDK's Host matcher does not. A proxy that
    forwards the Host header verbatim sends whatever case the operator wrote, so that
    spelling has to be admitted too — only ever the name they supplied, never a wider
    one.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if netloc.startswith("["):
        raw_hostname = netloc[1 : netloc.index("]")]
    else:
        raw_hostname = netloc.rsplit(":", 1)[0] if parts.port else netloc
    return _format_host(raw_hostname, parts.port)


def _allowed_hosts(options: StackOptions) -> str:
    """Host header values this deployment answers to.

    A client reaches it at whatever `--bind` says, so that name is admitted along
    with the loopback spellings. Behind a proxy the client sends the proxy's own
    hostname instead, and the SDK answers 421 to a Host it was not told about — so
    `--external-url` is what puts that name here. Asking for it while provisioning is
    what keeps SEC-2026-0063 from being a thing to remember afterwards.

    The loopback entries stay in every case: the container's own healthcheck and the
    in-container first-memory check both go through localhost.
    """
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    if options.publish and options.bind not in ("127.0.0.1", "0.0.0.0", "localhost"):
        hosts.append(f"{options.bind}:*")
    if options.external_url:
        parts = urlsplit(options.external_url)
        if parts.hostname:
            for host in dict.fromkeys(
                (
                    _format_host(parts.hostname, parts.port),
                    _raw_external_host(options.external_url),
                )
            ):
                if host is None:
                    continue
                hosts.append(host)
                # A proxy that forwards a non-default port sends `name:port`; one on
                # 80 or 443 sends the bare name. Both are the same deployment.
                if parts.port is None:
                    hosts.append(f"{host}:*")
    return ",".join(hosts)


def client_url(options: StackOptions) -> str:
    """The URL an MCP client puts in its configuration.

    With no published port `localhost` is wrong and there is nothing memcp can derive
    in its place — only the operator knows the hostname their platform routes, which
    is why `--no-publish` asks for it rather than printing a URL that does not work.
    """
    if options.external_url:
        base = options.external_url.rstrip("/")
        return base if base.endswith("/mcp") else f"{base}/mcp"
    host = "localhost" if options.bind in ("127.0.0.1", "0.0.0.0") else options.bind
    return f"http://{host}:{options.port}/mcp"


def _networks(options: StackOptions) -> tuple[list[Network], tuple[str, ...]]:
    """The networks the deployment declares, and the ones memcp attaches to."""
    if not options.network:
        return [], ()
    return (
        [Network(DEFAULT_NETWORK), Network(options.network, external=True)],
        (DEFAULT_NETWORK, options.network),
    )


def _memcp_service(
    *,
    env: tuple[EnvVar, ...],
    options: StackOptions,
    volumes: tuple[VolumeMount, ...] = (),
    depends_on: tuple[str, ...] = (),
) -> Service:
    base_env = (
        EnvVar("MEMCP_AUTH_TOKENS", f"${{{MEMCP_TOKEN_VAR}}}:{DEFAULT_TENANT}", secret=True),
        # Inside the container memcp must listen on all interfaces or the published
        # port reaches nothing. What keeps it off the network is the host-side bind
        # below, plus a token that is always set.
        EnvVar("MEMCP_HOST", "0.0.0.0"),
        EnvVar("MEMCP_PORT", str(CONTAINER_PORT)),
        # DNS-rebinding protection, on explicitly. The SDK enables it on its own only
        # when the bind address is loopback, and inside a container the bind must be
        # 0.0.0.0 — so a provisioned deployment would otherwise validate no Host
        # header at all. The bearer token is what actually stops a rebinding attack;
        # this is the layer above it.
        EnvVar("MEMCP_ALLOWED_HOSTS", _allowed_hosts(options)),
        EnvVar("MEMCP_LOG_FORMAT", "json"),
    )
    return Service(
        name="memcp",
        build_context=options.build_context,
        build_dockerfile=options.build_dockerfile,
        env=base_env + env,
        # No port at all when publishing is off — `model.to_compose` then renders no
        # `ports` key, which is a strictly narrower surface than binding loopback.
        ports=(Port(options.bind, options.port, CONTAINER_PORT),) if options.publish else (),
        volumes=volumes,
        networks=_networks(options)[1],
        depends_on=depends_on,
        healthcheck=_memcp_healthcheck(),
        description="the MCP server your client connects to — the only way in",
    )


def in_memory_stack(options: StackOptions) -> Deployment:
    service = _memcp_service(
        env=(EnvVar("MEMCP_BACKEND", "in_memory"),),
        options=options,
    )
    return Deployment(
        backend="in_memory",
        project_name=options.project,
        services=[service],
        volumes=[],
        secrets=[MEMCP_TOKEN_SECRET],
        durable=False,
        notes=[
            "in_memory holds everything in the container's process memory. `memcp down` "
            "and any restart lose all of it. It is here for a smoke test, not for use.",
        ],
    )


def sqlite_stack(options: StackOptions) -> Deployment:
    data = VolumeMount(
        "memcp_data", "/data", "the SQLite file every memory is stored in — survives `down`"
    )
    service = _memcp_service(
        env=(
            EnvVar("MEMCP_BACKEND", "sqlite"),
            EnvVar("MEMCP_SQLITE_PATH", SQLITE_DATA_PATH),
        ),
        options=options,
        volumes=(data,),
    )
    return Deployment(
        backend="sqlite",
        project_name=options.project,
        services=[service],
        volumes=[data],
        secrets=[MEMCP_TOKEN_SECRET],
        durable=True,
        notes=[
            "No API key and no account: one container, one file, memories that survive "
            "a restart. Retrieval is word overlap, not embedding similarity, and "
            "add_memory stores text verbatim rather than extracting facts from it.",
        ],
    )


def mem0_stack(options: StackOptions) -> Deployment:
    pg_data = VolumeMount(
        "postgres_data",
        "/var/lib/postgresql/data",
        "pgvector's data directory — every mem0 memory and its embedding",
    )
    mem0_history = VolumeMount(
        "mem0_history", "/app/history", "mem0's per-memory change log (SQLite)"
    )

    postgres = Service(
        name="postgres",
        image=PGVECTOR.reference,
        env=(
            EnvVar("POSTGRES_USER", "mem0"),
            EnvVar("POSTGRES_PASSWORD", f"${{{POSTGRES_PASSWORD_VAR}}}", secret=True),
            EnvVar("POSTGRES_DB", "postgres"),
        ),
        volumes=(pg_data,),
        binds=(BindMount("./init-db.sh", "/docker-entrypoint-initdb.d/init-db.sh"),),
        shm_size="128mb",
        healthcheck={
            "test": ["CMD-SHELL", "pg_isready -q -U mem0 -d postgres"],
            "interval": "3s",
            "timeout": "5s",
            "retries": 20,
        },
        description="pgvector — mem0's vector store and application database",
    )

    mem0 = Service(
        name="mem0",
        build_context="./mem0-src/server",
        build_dockerfile="Dockerfile",
        command=[
            "sh",
            "-c",
            "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000",
        ],
        env=(
            EnvVar(
                OPENAI_API_KEY_VAR,
                f"${{{OPENAI_API_KEY_VAR}}}",
                secret=True,
                description="mem0's LLM and embedder provider",
            ),
            *((EnvVar("OPENAI_BASE_URL", options.llm_base_url),) if options.llm_base_url else ()),
            EnvVar("POSTGRES_HOST", "postgres"),
            EnvVar("POSTGRES_PORT", "5432"),
            EnvVar("POSTGRES_DB", "postgres"),
            EnvVar("POSTGRES_USER", "mem0"),
            EnvVar("POSTGRES_PASSWORD", f"${{{POSTGRES_PASSWORD_VAR}}}", secret=True),
            EnvVar("POSTGRES_COLLECTION_NAME", "memories"),
            EnvVar("APP_DB_NAME", "mem0_app"),
            # Authenticated even though nothing outside the compose network can reach
            # it. A private network is not an authentication mechanism (G3).
            EnvVar("ADMIN_API_KEY", f"${{{MEM0_ADMIN_KEY_VAR}}}", secret=True),
            EnvVar("JWT_SECRET", f"${{{MEM0_JWT_SECRET_VAR}}}", secret=True),
            EnvVar("AUTH_DISABLED", "false"),
            EnvVar("MEM0_TELEMETRY", "false"),
            EnvVar("HISTORY_DB_PATH", "/app/history/history.db"),
            EnvVar("PYTHONUNBUFFERED", "1"),
        ),
        volumes=(mem0_history,),
        depends_on=("postgres",),
        healthcheck={
            # Liveness only: /openapi.json needs no credential, so this says the HTTP
            # layer is up and nothing more. What proves the credential works is
            # memcp's own /health, which calls mem0 authenticated (GitHub #24).
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request;"
                "urllib.request.urlopen('http://localhost:8000/openapi.json')",
            ],
            "interval": "5s",
            "timeout": "10s",
            "retries": 60,
            "start_period": "20s",
        },
        description=(
            f"mem0's REST server, built from {MEM0_SOURCE_REPO} at {MEM0_SOURCE_PIN[:7]}"
        ),
    )

    memcp = _memcp_service(
        env=(
            EnvVar("MEMCP_BACKEND", "mem0"),
            EnvVar("MEM0_API_BASE", "http://mem0:8000"),
            EnvVar("MEM0_API_KEY", f"${{{MEM0_ADMIN_KEY_VAR}}}", secret=True),
        ),
        options=options,
        depends_on=("mem0",),
    )

    return Deployment(
        backend="mem0",
        project_name=options.project,
        services=[postgres, mem0, memcp],
        volumes=[pg_data, mem0_history],
        secrets=[
            MEMCP_TOKEN_SECRET,
            RequiredSecret(
                POSTGRES_PASSWORD_VAR,
                minted=True,
                description="pgvector's superuser password, used only inside the stack",
            ),
            RequiredSecret(
                MEM0_ADMIN_KEY_VAR,
                minted=True,
                description="the credential memcp authenticates to mem0 with",
            ),
            RequiredSecret(
                MEM0_JWT_SECRET_VAR,
                minted=True,
                description="mem0's JWT signing secret; memcp does not use this path",
            ),
            _llm_secret(options),
        ],
        durable=True,
        notes=[
            "Neither postgres nor mem0 publishes a host port. The only route to stored "
            "memories is memcp's bearer gate, which is what makes tenant isolation mean "
            "anything.",
            f"The mem0 source is cloned to ./mem0-src at {MEM0_SOURCE_PIN} and built "
            "locally. First `up` builds it and is the slow one.",
        ],
    )


def _cognee_llm_secret(options: StackOptions) -> RequiredSecret:
    """Who supplies cognee's model access — the operator, or nobody.

    Cognee needs two models, not one: an LLM to extract the graph and an embedder to
    make anything retrievable. Both go through the same OpenAI-compatible client and
    the same key, so a local endpoint replaces both at once and a missing key stops
    both. Unlike mem0, cognee has no path that stores something useful without them.
    """
    if options.llm_base_url:
        return RequiredSecret(
            COGNEE_LLM_API_KEY_VAR,
            minted=True,
            description=(
                f"placeholder for the local endpoint at {options.llm_base_url}, which "
                "does not check it — cognee's client library requires the variable to exist"
            ),
        )
    return RequiredSecret(
        COGNEE_LLM_API_KEY_VAR,
        minted=False,
        description=(
            "cognee extracts a knowledge graph and embeds every memory through an LLM "
            "provider. Neither happens without a key, and a memory that is not embedded "
            "is not findable."
        ),
        how_to_obtain=(
            "Create a key at https://platform.openai.com/api-keys, then export "
            f"{COGNEE_LLM_API_KEY_VAR}=sk-... — it is a metered account, so this "
            "deployment costs money per memory written, on both an extraction call and "
            "an embedding call. To keep the money out of it, point --llm-base-url at a "
            "local OpenAI-compatible endpoint; how well cognee extracts against a small "
            "local model is not something this repository has measured. `--backend "
            "sqlite` needs no model at all, and gives up the graph and semantic search."
        ),
    )


def cognee_stack(options: StackOptions) -> Deployment:
    """memcp in front of a cognee server holding an embedded graph, vector and SQL store.

    Cognee ships all three databases inside its own image — Kuzu for the graph, LanceDB
    for vectors, SQLite for the rest — so this stack is two containers and one volume
    rather than mem0's three and two. Everything cognee persists lives under
    /app/.cognee_system and /app/.data_storage on that volume.
    """
    cognee_data = VolumeMount(
        "cognee_data",
        "/app/.cognee_system",
        "cognee's graph (Kuzu), vector index (LanceDB) and relational store (SQLite)",
    )
    cognee_files = VolumeMount(
        "cognee_files", "/app/.data_storage", "the raw text of every memory, as cognee stores it"
    )

    engine = Service(
        name="cognee",
        image=COGNEE.reference,
        env=(
            # Multi-tenant mode, explicitly. This is memcp's entire tenant boundary on
            # this backend: memcp derives one cognee account per tenant and cognee is
            # what keeps them apart. Turned off, cognee serves every request as one
            # default user and fifteen agents share one memory — so memcp's own
            # /health probes for it and refuses to come up healthy without it.
            EnvVar("ENABLE_BACKEND_ACCESS_CONTROL", "true"),
            EnvVar("REQUIRE_AUTHENTICATION", "true"),
            EnvVar("LLM_PROVIDER", "custom" if options.llm_base_url else "openai"),
            EnvVar("LLM_MODEL", "openai/gpt-4o-mini"),
            EnvVar("LLM_API_KEY", f"${{{COGNEE_LLM_API_KEY_VAR}}}", secret=True),
            EnvVar("EMBEDDING_PROVIDER", "openai"),
            EnvVar("EMBEDDING_MODEL", "openai/text-embedding-3-small"),
            EnvVar("EMBEDDING_API_KEY", f"${{{COGNEE_LLM_API_KEY_VAR}}}", secret=True),
            EnvVar("EMBEDDING_DIMENSIONS", "1536"),
            # Both endpoints are named in the plan when set, because they are where
            # every memory's text is sent (C6 — a plan that hides an egress destination
            # is the criterion failing).
            *(
                (
                    EnvVar("LLM_ENDPOINT", options.llm_base_url),
                    EnvVar("EMBEDDING_ENDPOINT", options.llm_base_url),
                )
                if options.llm_base_url
                else ()
            ),
            EnvVar("TELEMETRY_DISABLED", "true"),
            EnvVar("ENV", "prod"),
        ),
        volumes=(cognee_data, cognee_files),
        healthcheck={
            # Liveness only: /health needs no credential and says the HTTP layer is up.
            # What proves the credential and the tenant partitioning work is memcp's own
            # /health, which authenticates as a tenant and checks that an unauthenticated
            # read is refused.
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')",
            ],
            "interval": "5s",
            "timeout": "10s",
            "retries": 60,
            "start_period": "30s",
        },
        description=f"cognee {COGNEE.tag} — the knowledge graph, and the only thing that has one",
    )

    memcp = _memcp_service(
        env=(
            EnvVar("MEMCP_BACKEND", "cognee"),
            EnvVar("COGNEE_API_BASE", "http://cognee:8000"),
            EnvVar(
                COGNEE_TENANT_SECRET_VAR,
                f"${{{COGNEE_TENANT_SECRET_VAR}}}",
                secret=True,
                description="derives every tenant's cognee account",
            ),
        ),
        options=options,
        depends_on=("cognee",),
    )

    return Deployment(
        backend="cognee",
        project_name=options.project,
        services=[engine, memcp],
        volumes=[cognee_data, cognee_files],
        secrets=[
            MEMCP_TOKEN_SECRET,
            RequiredSecret(
                COGNEE_TENANT_SECRET_VAR,
                minted=True,
                description=(
                    "derives one cognee account per memcp tenant. Losing it does not lose "
                    "the memories, but a different value addresses different accounts, so "
                    "every tenant would come up empty"
                ),
            ),
            _cognee_llm_secret(options),
        ],
        durable=True,
        notes=[
            "cognee publishes no host port. The only route to stored memories is memcp's "
            "bearer gate, and cognee's own accounts are reachable only from inside the "
            "compose network.",
            "This is the backend with a real graph: memory_entities returns entities and "
            "the relationships between them, which is why the keyless stacks do not "
            "register that tool at all.",
            "Every add_memory runs cognee's extraction pipeline inside the request, so a "
            "write is slower here than on any other backend and returns only once the "
            "memory is findable.",
        ],
    )


_BUILDERS = {
    "in_memory": in_memory_stack,
    "sqlite": sqlite_stack,
    "mem0": mem0_stack,
    "cognee": cognee_stack,
}


def build(backend: str, options: StackOptions) -> Deployment:
    """Construct the declaration for a named backend."""
    try:
        builder = _BUILDERS[backend]
    except KeyError:
        raise ValueError(
            f"Unknown backend {backend!r}. Available: {', '.join(BACKENDS)}"
        ) from None
    deployment = builder(options)
    files = [
        "docker-compose.yml",
        ".env  (0600 — holds every credential)",
        ".gitignore  (makes this directory uncommittable)",
        "deployment.json  (how to reach this deployment; no credential in it)",
    ]
    if options.generates_dockerfile:
        files.append("memcp-image/Dockerfile")
    if backend == "mem0":
        files.append("init-db.sh")
        files.append("mem0-src/  (git clone of the pinned mem0 fork)")
    prefix = f"{options.directory}/" if options.directory else ""
    notes = list(deployment.notes)
    if not options.publish:
        notes.append(
            "This deployment publishes no host port. Nothing on the host can reach it "
            "directly; the platform routing to it — Dokploy, Coolify, Traefik, an "
            "ingress — has to reach the container over Docker, and `--smoke` and "
            "`verify` run their check from inside the container instead."
        )
    # Set once here rather than in each stack: how a deployment is reached is the same
    # question for every backend, and three copies of the answer is three chances for
    # the plan to describe something other than what `up` creates (C6).
    return replace(
        deployment,
        generated_files=[f"{prefix}{f}" for f in files],
        networks=_networks(options)[0],
        publish_host_port=options.publish,
        client_url=client_url(options),
        notes=notes,
    )
