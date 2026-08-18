"""`memcp up`, and the four commands around it.

    memcp plan            what would be created, before anything is
    memcp up              create it, wait for health, print the client snippet
    memcp status          what is running now
    memcp down            stop it; memories survive unless you pass --volumes
    memcp verify          store and retrieve one memory over MCP, and time it
    memcp rotate-token    mint a new bearer token for an existing deployment

`memcp serve`, and bare `python -m memcp`, still run the server itself. Provisioning
is a command an operator runs on a host — the running server never holds it, and
nothing it generates mounts the Docker socket (G1).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from memcp.deploy import runner
from memcp.deploy.model import Deployment
from memcp.deploy.runner import DeployError
from memcp.deploy.secretstore import MissingSecretError
from memcp.deploy.smoke import SmokeError, SmokeResult, first_memory
from memcp.deploy.stacks import (
    BACKENDS,
    DEFAULT_BACKEND,
    DEFAULT_PORT,
    LOOPBACK,
    MEMCP_TOKEN_VAR,
    external_host,
)

SUBCOMMANDS = ("up", "down", "plan", "status", "verify", "rotate-token", "serve")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memcp",
        description="Backend-agnostic MCP memory server, and the stacks it provisions.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="run the MCP server (the default with no subcommand)")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dir",
            default=runner.DEFAULT_DIR,
            help=f"deployment directory (default: {runner.DEFAULT_DIR})",
        )
        p.add_argument(
            "--project",
            default="memcp",
            help="compose project name — change it to run two deployments side by side",
        )

    def shape(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--backend",
            choices=BACKENDS,
            default=DEFAULT_BACKEND,
            help=f"memory backend to provision (default: {DEFAULT_BACKEND})",
        )
        p.add_argument("--port", type=int, default=DEFAULT_PORT, help="host port for memcp")
        p.add_argument(
            "--bind",
            default=LOOPBACK,
            help=(
                "host interface memcp publishes on (default: 127.0.0.1). Anything else "
                "exposes it beyond this machine and is your decision to make."
            ),
        )
        p.add_argument(
            "--no-publish",
            action="store_true",
            help=(
                "publish no host port at all. For a platform that routes into the "
                "container itself — Dokploy, Coolify, Kubernetes, your own Traefik — "
                "where a published port is a second, unrouted way in. Requires "
                "--external-url, and normally --network too."
            ),
        )
        p.add_argument(
            "--network",
            default=None,
            help=(
                "an existing Docker network to attach memcp to, as well as this "
                "deployment's own. Name the one your platform's router is on."
            ),
        )
        p.add_argument(
            "--external-url",
            default=None,
            help=(
                "the URL clients reach this deployment at, when a proxy or platform "
                "fronts it (https://memory.example.com). It is what the client "
                "snippet prints and what Host validation admits."
            ),
        )
        p.add_argument(
            "--llm-base-url",
            default=None,
            help=(
                "OpenAI-compatible endpoint for the LLM and embedder the mem0 and "
                "cognee backends need (Ollama, llama.cpp, LiteLLM). Set it and no "
                "provider account is needed; leave it unset and a provider key is "
                "required. How well either backend works against a small local model "
                "is not something this repository has measured."
            ),
        )
        p.add_argument(
            "--memcp-source",
            default="auto",
            help=(
                "where memcp's own image comes from: 'auto' (a checkout if you are in "
                "one, else PyPI), 'pypi', or a path to a memcp checkout"
            ),
        )

    up = sub.add_parser("up", help="provision the stack and wait for it to be healthy")
    common(up)
    shape(up)
    up.add_argument(
        "--timeout",
        type=int,
        default=runner.DEFAULT_TIMEOUT,
        help=f"seconds to wait for health before failing (default: {runner.DEFAULT_TIMEOUT})",
    )
    up.add_argument(
        "--smoke",
        action="store_true",
        help="after health, store and retrieve one memory over MCP and time it",
    )

    plan = sub.add_parser("plan", help="print everything that would be created")
    common(plan)
    shape(plan)
    plan.add_argument("--json", action="store_true", help="machine-readable plan")

    down = sub.add_parser("down", help="stop the deployment")
    common(down)
    down.add_argument(
        "--volumes",
        action="store_true",
        help="also delete the volumes — this destroys every stored memory",
    )

    status = sub.add_parser("status", help="show what is running")
    common(status)

    verify = sub.add_parser(
        "verify", help="store and retrieve one memory over MCP against a running deployment"
    )
    common(verify)
    verify.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"host port to reach (default: whatever `up` published, else {DEFAULT_PORT})",
    )
    verify.add_argument(
        "--url",
        default=None,
        help=(
            "check over this MCP endpoint instead — the external URL a proxy serves, "
            "which is the one route no check run on the host can exercise for you"
        ),
    )

    rotate = sub.add_parser("rotate-token", help="mint a new bearer token")
    common(rotate)

    return parser


def _shape_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Every flag that changes what the deployment *is*, read in one place.

    `plan` and `up` have to build the same `Deployment` from the same flags, or the
    plan describes something other than what gets created — and C6 is the criterion
    that makes provisioning auditable, so that is the criterion failing rather than a
    cosmetic mismatch.

    It was a real bug: `_plan` passed five of the six and dropped `llm_base_url`, so
    `plan --backend mem0 --llm-base-url ...` reported `OPENAI_API_KEY` as the
    operator's to supply and never mentioned `OPENAI_BASE_URL`, while `up` with the
    identical flags minted a placeholder and pointed mem0's LLM at that endpoint. The
    plan omitted an egress destination the deployment would configure.

    One function rather than two call sites is what makes the next flag structural
    instead of something to remember; `tests/test_deploy_cli.py` asserts both that the
    two commands accept the same flags and that they produce identical deployments.
    """
    _check_reachability(args)
    return {
        "port": args.port,
        "bind": args.bind,
        "publish": not args.no_publish,
        "network": args.network,
        "external_url": args.external_url,
        "project": args.project,
        "source_spec": args.memcp_source,
        "llm_base_url": args.llm_base_url,
    }


def _check_reachability(args: argparse.Namespace) -> None:
    """Refuse a deployment nothing could reach, and one nothing would admit.

    With no published port, `localhost:8080` is not the address any more and memcp
    cannot derive what is — only the operator knows the hostname their platform
    routes. Asking for it here is also what sets MEMCP_ALLOWED_HOSTS to that name:
    the SDK answers 421 to a Host it was not told about, and a proxied deployment is
    exactly where that bites (SEC-2026-0063).
    """
    if args.external_url and not external_host(args.external_url):
        raise DeployError(
            f"--external-url {args.external_url} is not a URL memcp can read a hostname "
            "from. Give it scheme and host, as a client would: "
            "--external-url https://memory.example.com"
        )
    if args.no_publish and not args.external_url:
        raise DeployError(
            "--no-publish leaves nothing on this host to connect to, so memcp needs to "
            "be told the address clients will use.\n\n"
            "  Behind a platform or proxy:  --external-url https://memory.example.com\n"
            "  Reached only by other containers on the network: "
            "--external-url http://memcp:8080\n\n"
            "It is the URL the client snippet prints, and the Host header this "
            "deployment will admit."
        )


def _plan(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    deployment, source = runner.plan_for(args.backend, directory, **_shape_kwargs(args))
    if args.json:
        print(deployment.to_json())
        return 0
    print(deployment.render_plan())
    print(f"memcp image: {source.describe}")
    print()
    print("Nothing above exists yet. `memcp up` is what creates it.")
    return 0


def _up(args: argparse.Namespace) -> int:
    started = time.monotonic()
    directory = Path(args.dir)
    runner.require_docker()

    deployment, source, values, minted_names = runner.prepare(
        args.backend, directory, **_shape_kwargs(args)
    )

    existing = runner.read_token(directory) is not None
    print(f"memcp {'up' if existing else 'first run'} — backend {deployment.backend}")
    print(f"  deployment directory  {directory}")
    print(f"  memcp image           {source.describe}")
    if not deployment.durable:
        print("  WARNING               in_memory loses every memory on restart")
    print()

    runner.materialize(deployment, directory, source, values)
    runner.compose_up(directory, args.timeout)

    token = values[MEMCP_TOKEN_VAR]
    print()
    print(f"Healthy in {runner.elapsed_since(started)}.")

    if args.smoke:
        try:
            result, how = _check_deployment(deployment, directory, token)
        except SmokeError as e:
            print(f"\nFirst-memory check FAILED: {e}", file=sys.stderr)
            return 1
        print(
            f"First memory stored and retrieved over MCP in {result.seconds:.2f}s "
            f"(total from command start: {runner.elapsed_since(started)})."
        )
        print(f"  {how}")

    print()
    if minted_names:
        # Names, never values: the only credential this command prints is the memcp
        # token, in the client snippet below, which is what C4 asks it to do.
        print(f"Minted this run: {', '.join(minted_names)}. Shown once, nowhere else.")
    print("Add this to your MCP client configuration:")
    print()
    print(runner.client_snippet(token, deployment.client_url))
    print()
    if not deployment.publish_host_port:
        print(
            "No host port is published: that URL works once your platform routes it to "
            f"this container on port {deployment.container_port}. Until then nothing "
            "outside Docker can reach it, which is what you asked for."
        )
        print()
    print(f"Stop it with `memcp down --dir {directory}`. Memories survive that.")
    return 0


def _down(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    compose_file = directory / runner.COMPOSE_FILENAME
    if not compose_file.exists():
        raise DeployError(f"no deployment at {directory} — nothing to stop.")
    runner.require_docker()
    if args.volumes:
        print("Deleting volumes: every stored memory in this deployment goes with them.")
    runner.compose_down(directory, volumes=args.volumes)
    print(
        "Stopped."
        if not args.volumes
        else "Stopped and deleted. Nothing of this deployment remains."
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    if not (directory / runner.COMPOSE_FILENAME).exists():
        print(f"No deployment at {directory}.")
        return 1
    runner.require_docker()
    return runner.compose_ps(directory)


def _first_memory(token: str, port: int) -> SmokeResult:
    return first_memory(f"http://127.0.0.1:{port}/mcp", token)


def _check_deployment(
    deployment: Deployment, directory: Path, token: str
) -> tuple[SmokeResult, str]:
    """Store and retrieve one memory, by whichever route this deployment has.

    A published port is checked from the host, over the same route a client takes.
    With publishing off there is no such route, so the check runs inside the
    container — and the sentence it returns says which of the two happened. A check
    that skipped the platform's own routing must not read as one that covered it.
    """
    if deployment.publish_host_port:
        port = deployment.memcp_service.ports[0].host_port
        return _first_memory(token, port), (
            f"Checked over the published port at 127.0.0.1:{port} — the same route a client takes."
        )
    result = runner.smoke_inside_container(directory, token, deployment.container_port)
    return result, (
        "Checked from inside the container (`docker compose exec`), because no host "
        f"port is published. What this did not check is the route to {deployment.client_url} "
        "— that is your platform's, and `memcp verify --url` is how you check it once "
        "it is up."
    )


def _verify(args: argparse.Namespace) -> int:
    """The same check as `up --smoke`, against a deployment already running.

    Which route it takes is read from what `up` recorded, not assumed: a deployment
    provisioned with --no-publish has no host port, and dialling localhost anyway
    would report a connection failure that says nothing about the deployment.
    """
    directory = Path(args.dir)
    token = runner.read_token(directory)
    if token is None:
        raise DeployError(f"no deployment at {directory} — run `memcp up` first.")
    state = runner.read_state(directory)

    if args.url:
        url, how = args.url, f"over {args.url}"
    elif args.port is not None:
        url, how = f"http://127.0.0.1:{args.port}/mcp", f"over the host port {args.port}"
    elif state and not state.publish_host_port:
        url, how = None, "from inside the container — this deployment publishes no host port"
    elif state and state.host_url:
        url, how = state.host_url, f"over the published port {state.host_port}"
    else:
        url, how = f"http://127.0.0.1:{DEFAULT_PORT}/mcp", f"over the host port {DEFAULT_PORT}"

    try:
        if url is None:
            container_port = state.container_port if state else DEFAULT_PORT
            result = runner.smoke_inside_container(directory, token, container_port)
        else:
            result = first_memory(url, token)
    except SmokeError as e:
        print(f"First-memory check FAILED ({how}): {e}", file=sys.stderr)
        return 1
    print(f"add_memory then search_memory succeeded over MCP in {result.seconds:.2f}s, {how}.")
    if url is None and state and state.client_url:
        print(
            f"That is the deployment, not the route to it: whether {state.client_url} "
            "reaches this container is your platform's to answer, and `memcp verify "
            "--url` asks it."
        )
    return 0


def _rotate(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    token = runner.rotate_token(directory)
    print("New token minted. The previous one no longer works.")
    print("Printed once — it is stored only in the deployment's .env.")
    print()
    print(f"  {token}")
    print()
    print(
        f"Apply it with `memcp up --dir {directory}` (recreates the container with the "
        "new value), then update every MCP client."
    )
    return 0


HANDLERS = {
    "up": _up,
    "down": _down,
    "plan": _plan,
    "status": _status,
    "verify": _verify,
    "rotate-token": _rotate,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = HANDLERS.get(args.command or "")
    if handler is None:  # pragma: no cover - dispatched before reaching here
        parser.print_help()
        return 2
    try:
        return handler(args)
    except (DeployError, MissingSecretError) as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
