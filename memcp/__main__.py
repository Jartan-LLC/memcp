"""python -m memcp entrypoint."""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from memcp.config import Config
from memcp.logging import setup_logging
from memcp.server import create_app

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        config = Config()
    except ValidationError as e:
        print(f"Configuration error:\n{e}", file=sys.stderr)
        raise SystemExit(1) from None
    setup_logging(level=config.log_level, fmt=config.log_format)

    if not config.memcp_auth_tokens:
        logger.warning("MEMCP_AUTH_TOKENS is unset — the MCP endpoint is UNAUTHENTICATED.")

    try:
        app, _backend = create_app(config)
    except ValueError as e:
        logger.critical("Configuration error: %s", e)
        raise SystemExit(1) from None
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    main()
