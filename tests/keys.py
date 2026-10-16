"""Keys for tests: one to act as, and a keys file on disk."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from memcp.identity import Grant, Key, Relation


def make_key(space: str, relation: Relation = "writer", name: str | None = None) -> Key:
    """A key granted `relation` on `space`, named after the space unless `name` is given."""
    return Key(name=name or space, grant=Grant(space=space, relation=relation))


def secret(token: str) -> str:
    """The keys-file form of `token`."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def write_keys_file(path: Path, *entries: tuple[str, str, str, str]) -> Path:
    """Write a keys file of `(name, token, space, relation)` entries to `path`."""
    blocks = [
        "\n".join(
            [
                "[[keys]]",
                f"name = {json.dumps(name)}",
                f'secrets = ["{secret(token)}"]',
                f'grants = [{{ space = {json.dumps(space)}, relation = "{relation}" }}]',
            ]
        )
        for name, token, space, relation in entries
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path
