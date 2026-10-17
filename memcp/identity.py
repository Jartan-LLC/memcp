"""The current request's key, propagated through a context variable.

A `Key` is a configured identity: its name is the author of every write it makes, and
its one `Grant` names the space it reaches and the relation it holds there. The current
key has no default: reading it when none is set raises `LookupError`, so nothing can
run as a key it wasn't given.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal

Relation = Literal["reader", "writer"]


@dataclass(frozen=True)
class Grant:
    """Permission to use one space with one relation."""

    space: str
    relation: Relation


@dataclass(frozen=True)
class Key:
    """A configured identity: a name and the grant it holds. Never carries a secret."""

    name: str
    grant: Grant


_key_var: ContextVar[Key] = ContextVar("key")


def current_key() -> Key:
    """The current request's key. Raises LookupError when none is set."""
    return _key_var.get()


def set_key(key: Key) -> Token[Key]:
    """Make `key` the current request's key. Returns a token for `reset_key`."""
    return _key_var.set(key)


def reset_key(token: Token[Key]) -> None:
    """Restore the key that was current before `set_key` returned `token`."""
    _key_var.reset(token)
