"""`python -m memcp.conformance` — the one documented command (A1).

Runs the suite from wherever memcp is installed, so an out-of-tree adapter needs
this repository's tests no more than it needs its source.
"""

from __future__ import annotations

import sys

SUITE = "memcp.conformance.suite"


def main(argv: list[str] | None = None) -> int:
    try:
        import pytest
    except ImportError:  # pragma: no cover - depends on the install extra
        print(
            "The conformance suite needs pytest and pytest-asyncio: "
            'pip install "memcp-server[dev]"',
            file=sys.stderr,
        )
        return 2
    args = ["--pyargs", SUITE, *(argv if argv is not None else sys.argv[1:])]
    return int(pytest.main(args))


if __name__ == "__main__":
    raise SystemExit(main())
