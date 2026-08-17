"""A YAML writer for exactly the document `Deployment.to_compose()` produces.

memcp does not depend on a YAML library and this is not a reason to add one. The
value space here is closed — dict, list, str, int, bool, None — and every scalar is
written double-quoted, which needs no knowledge of YAML's plain-scalar rules. What
comes out is ordinary readable compose YAML that a person can check against
`memcp plan`.

Round-tripping is asserted in tests against Python's own parse of the JSON subset,
and the file itself is exercised end to end by the provisioning job in CI.
"""

from __future__ import annotations

from typing import Any

_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def quote(value: str) -> str:
    out = "".join(_ESCAPES.get(ch, ch) for ch in value)
    return f'"{out}"'


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return quote(str(value))


def dump(data: Any, indent: int = 0) -> str:
    """Render a nested dict/list/scalar structure as block-style YAML."""
    pad = "  " * indent
    lines: list[str] = []

    if isinstance(data, dict):
        if not data:
            return f"{pad}{{}}\n"
        for key, value in data.items():
            if isinstance(value, dict | list):
                if value:
                    lines.append(f"{pad}{key}:")
                    lines.append(dump(value, indent + 1).rstrip("\n"))
                else:
                    lines.append(f"{pad}{key}: {'{}' if isinstance(value, dict) else '[]'}")
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")
        return "\n".join(lines) + "\n"

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict | list):
                nested = dump(item, indent + 1).rstrip("\n")
                # Splice the dash onto the first line of the nested block.
                first, _, rest = nested.partition("\n")
                lines.append(f"{pad}- {first.strip()}")
                if rest:
                    lines.append(rest)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
        return "\n".join(lines) + "\n"

    return f"{pad}{_scalar(data)}\n"


def render(compose_doc: dict[str, Any], header: str) -> str:
    """The compose file, with a header saying where it came from."""
    return header + dump(compose_doc)
