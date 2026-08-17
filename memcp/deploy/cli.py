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
from memcp.deploy.runner import DeployError
from memcp.deploy.secretstore import MissingSecretError
from memcp.deploy.smoke import SmokeError, first_memory
from memcp.deploy.stacks import (
    BACKENDS,
    DEFAULT_BACKEND,
    DEFAULT_PORT,
    LOOPBACK,
    MEMCP_TOKEN_VAR,
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
    verify.add_argument("--port", type=int, default=DEFAULT_PORT, help="host port to reach")

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
    return {
        "port": args.port,
        "bind": args.bind,
        "project": args.project,
        "source_spec": args.memcp_source,
        "llm_base_url": args.llm_base_url,
    }


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
            result = _first_memory(token, args.port)
        except SmokeError as e:
            print(f"\nFirst-memory check FAILED: {e}", file=sys.stderr)
            return 1
        print(
            f"First memory stored and retrieved over MCP in {result.seconds:.2f}s "
            f"(total from command start: {runner.elapsed_since(started)})."
        )

    print()
    if minted_names:
        # Names, never values: the only credential this command prints is the memcp
        # token, in the client snippet below, which is what C4 asks it to do.
        print(f"Minted this run: {', '.join(minted_names)}. Shown once, nowhere else.")
    print("Add this to your MCP client configuration:")
    print()
    print(runner.client_snippet(token, args.bind, args.port))
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


def _first_memory(token: str, port: int):
    return first_memory(f"http://127.0.0.1:{port}/mcp", token)


def _verify(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    token = runner.read_token(directory)
    if token is None:
        raise DeployError(f"no deployment at {directory} — run `memcp up` first.")
    try:
        result = _first_memory(token, args.port)
    except SmokeError as e:
        print(f"First-memory check FAILED: {e}", file=sys.stderr)
        return 1
    print(f"add_memory then search_memory succeeded over MCP in {result.seconds:.2f}s.")
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
