"""python -m memcp entrypoint.

With no subcommand, or with `serve`, this runs the MCP server — unchanged, so the
Docker image's entrypoint and every existing deployment keep working. The
provisioning subcommands live in memcp.deploy and never run inside the server.
"""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from memcp.config import Config, is_loopback
from memcp.logging import setup_logging
from memcp.server import create_app

logger = logging.getLogger(__name__)

DEPLOY_COMMANDS = ("up", "down", "plan", "status", "verify", "rotate-token")


def serve() -> None:
    try:
        config = Config()
    except ValidationError as e:
        print(f"Configuration error:\n{e}", file=sys.stderr)
        raise SystemExit(1) from None
    setup_logging(level=config.log_level, fmt=config.log_format)

    if not config.memcp_auth_tokens:
        # Config refuses this combination on any non-loopback bind, so reaching here
        # means a local dev server. Still worth saying out loud.
        logger.warning(
            "MEMCP_AUTH_TOKENS is unset — the MCP endpoint is UNAUTHENTICATED and every "
            "request is served as one tenant. Bound to %s, so nothing off this machine "
            "can reach it.",
            config.host,
        )
        if not is_loopback(config.host):  # pragma: no cover - Config raises first
            raise SystemExit(1)

    try:
        app, _backend = create_app(config)
    except ValueError as e:
        logger.critical("Configuration error: %s", e)
        raise SystemExit(1) from None
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    first = args[0] if args else ""

    if first in DEPLOY_COMMANDS or first in ("-h", "--help"):
        from memcp.deploy.cli import main as deploy_main

        raise SystemExit(deploy_main(args))
    if first == "serve":
        args = args[1:]
    if args:
        print(
            f"Unknown argument: {args[0]!r}. Run `memcp --help` for the commands this "
            "supports; the server itself is configured through environment variables.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    serve()


if __name__ == "__main__":
    main()
