"""What a deployment is, before anything is created.

One declarative object describes the whole stack: services, volumes, published
ports, environment, and which environment values are secrets. Three things are
rendered from it and nothing else — the compose file, the plan `memcp plan` prints
(C6), and the list of secrets that have to exist before `up` can run.

Keeping those three from drifting is the point. A service added to the compose file
but not to the plan would be a container created without being declared, which is
exactly what C6 forbids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

# The value a secret-bearing environment entry carries in the compose file: an
# interpolation from the deployment's .env, never a literal. G4 — the secret prints
# once at mint time and lands in one 0600 file.
SECRET_PLACEHOLDER = "<from .env, not shown>"


@dataclass(frozen=True)
class EnvVar:
    """One environment entry on a service.

    `secret` marks a value that must never be rendered into plan output or logs.
    `required_from_operator` marks one the operator has to supply — a provider API
    key memcp cannot mint (C3).
    """

    name: str
    value: str
    secret: bool = False
    description: str = ""


@dataclass(frozen=True)
class Port:
    """A published port. `host_ip` is mandatory — there is no all-interfaces default."""

    host_ip: str
    host_port: int
    container_port: int

    def render(self) -> str:
        return f"{self.host_ip}:{self.host_port}:{self.container_port}"


@dataclass(frozen=True)
class Network:
    """A Docker network the deployment attaches to.

    `external` names one that already exists and that memcp must neither create nor
    delete — the network a platform's own router lives on, which is how a deployment
    publishing no host port is still reachable.
    """

    name: str
    external: bool = False


@dataclass(frozen=True)
class VolumeMount:
    name: str
    path: str
    description: str = ""


@dataclass(frozen=True)
class BindMount:
    """A file generated into the deployment directory and mounted read-only."""

    source: str
    path: str


@dataclass(frozen=True)
class Service:
    name: str
    image: str | None = None
    build_context: str | None = None
    build_dockerfile: str | None = None
    command: list[str] | None = None
    env: tuple[EnvVar, ...] = ()
    ports: tuple[Port, ...] = ()
    volumes: tuple[VolumeMount, ...] = ()
    binds: tuple[BindMount, ...] = ()
    networks: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    healthcheck: dict[str, Any] | None = None
    shm_size: str | None = None
    description: str = ""

    @property
    def source(self) -> str:
        if self.image:
            return self.image
        return f"build {self.build_context}"


@dataclass(frozen=True)
class RequiredSecret:
    """A value that must exist in the deployment's .env before `up` runs.

    `minted` values memcp generates itself. Everything else the operator supplies,
    and `up` refuses to start a half-configured stack rather than inventing one
    (C3, C4, G3).
    """

    name: str
    minted: bool
    description: str
    # How the operator gets one, when memcp cannot mint it.
    how_to_obtain: str = ""


@dataclass
class Deployment:
    """A named stack: what it runs, what it stores, what it needs first."""

    backend: str
    project_name: str
    services: list[Service] = field(default_factory=list)
    volumes: list[VolumeMount] = field(default_factory=list)
    secrets: list[RequiredSecret] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    durable: bool = True
    notes: list[str] = field(default_factory=list)
    networks: list[Network] = field(default_factory=list)
    # Whether memcp publishes a host port at all. False is something the operator
    # typed, and the plan says so in those words — an unpublished port that reads
    # like an omission is the one thing this must never look like.
    publish_host_port: bool = True
    # The URL a client puts in its MCP configuration. With no published port that is
    # the platform's own hostname, which only the operator knows.
    client_url: str = ""

    @property
    def memcp_service(self) -> Service:
        return next(s for s in self.services if s.name == "memcp")

    @property
    def container_port(self) -> int:
        """The port memcp listens on inside its container, published or not."""
        for env in self.memcp_service.env:
            if env.name == "MEMCP_PORT":
                return int(env.value)
        return 8080

    @property
    def operator_secrets(self) -> list[RequiredSecret]:
        return [s for s in self.secrets if not s.minted]

    @property
    def minted_secrets(self) -> list[RequiredSecret]:
        return [s for s in self.secrets if s.minted]

    # -- compose ---------------------------------------------------------------

    def to_compose(self) -> dict[str, Any]:
        """The compose document. Secret values are `${VAR}`, resolved from .env."""
        services: dict[str, Any] = {}
        for svc in self.services:
            entry: dict[str, Any] = {}
            if svc.image:
                entry["image"] = svc.image
            if svc.build_context:
                build: dict[str, Any] = {"context": svc.build_context}
                if svc.build_dockerfile:
                    build["dockerfile"] = svc.build_dockerfile
                entry["build"] = build
            if svc.command:
                entry["command"] = svc.command
            if svc.env:
                entry["environment"] = {e.name: e.value for e in svc.env}
            if svc.ports:
                entry["ports"] = [p.render() for p in svc.ports]
            mounts = [f"{v.name}:{v.path}" for v in svc.volumes]
            mounts += [f"{b.source}:{b.path}:ro" for b in svc.binds]
            if mounts:
                entry["volumes"] = mounts
            if svc.networks:
                entry["networks"] = list(svc.networks)
            if svc.depends_on:
                entry["depends_on"] = {
                    dep: {"condition": "service_healthy"} for dep in svc.depends_on
                }
            if svc.healthcheck:
                entry["healthcheck"] = svc.healthcheck
            if svc.shm_size:
                entry["shm_size"] = svc.shm_size
            entry["restart"] = "unless-stopped"
            services[svc.name] = entry

        doc: dict[str, Any] = {"name": self.project_name, "services": services}
        if self.volumes:
            doc["volumes"] = {v.name: None for v in self.volumes}
        if self.networks:
            # `null` leaves compose's own defaults in place, which is what the project
            # network wants; `external: true` says join it, do not create it.
            doc["networks"] = {
                n.name: ({"external": True} if n.external else None) for n in self.networks
            }
        return doc

    def _network_phrase(self) -> str:
        """How the plan names the networks memcp can be reached over."""
        if not self.networks:
            return "this deployment's default compose network"
        return "Docker network(s) " + ", ".join(
            f"{n.name} (external)" if n.external else n.name for n in self.networks
        )

    # -- plan (C6) -------------------------------------------------------------

    def render_plan(self) -> str:
        """Everything this deployment will create, before it creates any of it.

        No secret value appears here, minted or supplied — a plan is something you
        paste into an issue.
        """
        out: list[str] = []
        out.append(f"memcp deployment plan — backend {self.backend!r}")
        out.append(f"compose project: {self.project_name}")
        out.append("")

        out.append("CONTAINERS")
        for svc in self.services:
            out.append(f"  {svc.name}")
            out.append(f"    image     {svc.source}")
            if svc.description:
                out.append(f"    role      {svc.description}")
        out.append("")

        out.append("PUBLISHED PORTS")
        published = [(s, p) for s in self.services for p in s.ports]
        if not self.publish_host_port:
            # Said in the words the operator typed, because the difference between a
            # port deliberately unpublished and a plan that forgot to mention one is
            # the whole of what this section is read for.
            out.append("  (none — publishing is off, because you asked for it: --no-publish)")
            out.append(
                f"  memcp listens on container port {self.container_port}, reachable only "
                "from inside Docker,"
            )
            out.append(f"  over {self._network_phrase()}.")
            out.append("  Whatever routes to this deployment has to be on one of those networks.")
        elif not published:
            out.append("  (none)")
        for svc, port in published:
            out.append(f"  {port.host_ip}:{port.host_port} -> {svc.name}:{port.container_port}")
        # memcp's own absence is stated above, in the words that say it was chosen.
        internal = [
            s.name
            for s in self.services
            if not s.ports and not (s.name == "memcp" and not self.publish_host_port)
        ]
        if internal:
            out.append(f"  no host port, internal network only: {', '.join(sorted(internal))}")
        out.append("")

        if self.networks:
            out.append("NETWORKS")
            for network in self.networks:
                if network.external:
                    out.append(
                        f"  {network.name}  existing network, joined not created — memcp "
                        "attaches to it"
                    )
                else:
                    out.append(f"  {network.name}  created by compose for this deployment")
            out.append("")

        if self.client_url:
            out.append("CLIENTS REACH IT AT")
            out.append(f"  {self.client_url}")
            if not self.publish_host_port:
                out.append(
                    "  Routing that URL to this container is the platform's job, not memcp's."
                )
            out.append("")

        out.append("VOLUMES")
        if not self.volumes:
            out.append("  (none — nothing survives `memcp down`)")
        for vol in self.volumes:
            mounted = ", ".join(
                f"{s.name}:{v.path}"
                for s in self.services
                for v in s.volumes
                if v.name == vol.name
            )
            out.append(f"  {vol.name}  {vol.description}")
            out.append(f"    mounted at  {mounted}")
        out.append("")

        out.append("ENVIRONMENT")
        for svc in self.services:
            if not svc.env:
                continue
            out.append(f"  {svc.name}")
            for env in sorted(svc.env, key=lambda e: e.name):
                shown = SECRET_PLACEHOLDER if env.secret else env.value
                out.append(f"    {env.name}={shown}")
        out.append("")

        out.append("FILES WRITTEN")
        for path in self.generated_files:
            out.append(f"  {path}")
        out.append("")

        out.append("SECRETS")
        for secret in self.secrets:
            if secret.minted:
                out.append(f"  {secret.name}  minted by memcp — {secret.description}")
            else:
                out.append(f"  {secret.name}  REQUIRED from you — {secret.description}")
                if secret.how_to_obtain:
                    out.append(f"    {secret.how_to_obtain}")
        if not self.secrets:
            out.append("  (none)")
        out.append("")

        if not self.durable:
            out.append("DURABILITY")
            out.append("  This backend keeps memories in process memory. A restart of the")
            out.append("  memcp container loses every memory in it. For a durable stack with")
            out.append("  no API key, use --backend sqlite.")
            out.append("")

        for note in self.notes:
            out.append(f"NOTE  {note}")
        if self.notes:
            out.append("")

        return "\n".join(out).rstrip() + "\n"

    def to_json(self) -> str:
        """Machine-readable plan. Same redaction rule as render_plan."""
        return json.dumps(
            {
                "backend": self.backend,
                "project_name": self.project_name,
                "durable": self.durable,
                "publish_host_port": self.publish_host_port,
                "container_port": self.container_port,
                "client_url": self.client_url,
                "containers": [
                    {"name": s.name, "source": s.source, "role": s.description}
                    for s in self.services
                ],
                "ports": [
                    {
                        "service": s.name,
                        "host_ip": p.host_ip,
                        "host_port": p.host_port,
                        "container_port": p.container_port,
                    }
                    for s in self.services
                    for p in s.ports
                ],
                "networks": [{"name": n.name, "external": n.external} for n in self.networks],
                "volumes": [{"name": v.name, "purpose": v.description} for v in self.volumes],
                "environment": {
                    s.name: {e.name: (SECRET_PLACEHOLDER if e.secret else e.value) for e in s.env}
                    for s in self.services
                    if s.env
                },
                "files": list(self.generated_files),
                "secrets": [
                    {
                        "name": s.name,
                        "minted_by_memcp": s.minted,
                        "purpose": s.description,
                    }
                    for s in self.secrets
                ],
                "notes": list(self.notes),
            },
            indent=2,
        )


BindKind = Literal["file"]
