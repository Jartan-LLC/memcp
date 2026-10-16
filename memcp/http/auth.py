"""Authentication — the keys file, bearer token resolution and the ASGI gate.

Identity flows: Bearer token → its SHA-256 → `Key` → ContextVar (`memcp.identity`) →
policy. The keys file holds only digests, so nothing in it grants access.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tomllib
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from memcp.identity import Grant, Key, reset_key, set_key
from memcp.types import canonical_error

logger = logging.getLogger(__name__)

# Dev mode: no keys file, one local key, on loopback only (config.py refuses the rest).
LOCAL_KEY = Key(name="local", grant=Grant(space="default_user", relation="writer"))

_SECRET_PREFIX = "sha256:"


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _GrantEntry(_Entry):
    space: str
    relation: Literal["reader", "writer"]


class _KeyEntry(_Entry):
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    secrets: Annotated[
        list[Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]], Field(min_length=1)
    ]
    grants: Annotated[list[_GrantEntry], Field(min_length=1, max_length=1)]


class _KeysFile(_Entry):
    keys: Annotated[list[_KeyEntry], Field(min_length=1)]


class KeyTable:
    """The configured keys, found by the SHA-256 of a token."""

    def __init__(self, by_digest: dict[str, Key]):
        self._by_digest = by_digest

    def resolve(self, token: str) -> Key | None:
        """The key `token` belongs to, or None.

        A dict lookup on the digest: its timing can reveal only the digest, and turning
        that into a token needs a preimage.
        """
        return self._by_digest.get(hashlib.sha256(token.encode()).hexdigest())

    def keys(self) -> list[Key]:
        """Each configured key once, in file order."""
        return list(dict.fromkeys(self._by_digest.values()))


def _where(loc: tuple[int | str, ...]) -> str:
    """A pydantic error location as the file's terms: `key 2: grants.0.relation`."""
    if len(loc) >= 2 and loc[0] == "keys" and isinstance(loc[1], int):
        rest = ".".join(str(part) for part in loc[2:])
        return f"key {loc[1] + 1}" + (f": {rest}" if rest else "")
    return ".".join(str(part) for part in loc) or "the file"


def load_keys(path: str) -> KeyTable:
    """Read the keys file at `path`. Raises ValueError for anything startup refuses.

    Messages name a key by its position, never by a secret: they reach the startup log,
    and a malformed secret may be a token pasted where its hash belongs.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except OSError as e:
        raise ValueError(f"MEMCP_KEYS_FILE {path} can't be read: {e.strerror}") from None
    except UnicodeDecodeError:
        # Not the decoder's message: it quotes a byte of the file.
        raise ValueError(f"MEMCP_KEYS_FILE {path} isn't UTF-8") from None
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"MEMCP_KEYS_FILE {path} isn't valid TOML: {e}") from None
    try:
        parsed = _KeysFile.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{_where(err['loc'])}: {err['msg']}"
            for err in e.errors(include_url=False, include_context=False, include_input=False)
        )
        raise ValueError(f"MEMCP_KEYS_FILE {path}: {problems}") from None

    by_digest: dict[str, Key] = {}
    owner: dict[str, int] = {}
    names: dict[str, int] = {}
    for n, entry in enumerate(parsed.keys, 1):
        if entry.name in names:
            raise ValueError(
                f"MEMCP_KEYS_FILE {path}: keys {names[entry.name]} and {n} are both named "
                f"{entry.name!r}"
            )
        names[entry.name] = n
        grant = entry.grants[0]
        key = Key(name=entry.name, grant=Grant(space=grant.space, relation=grant.relation))
        for secret in entry.secrets:
            digest = secret.removeprefix(_SECRET_PREFIX)
            if owner.get(digest) == n:
                raise ValueError(f"MEMCP_KEYS_FILE {path}: key {n} lists the same secret twice")
            if digest in owner:
                raise ValueError(
                    f"MEMCP_KEYS_FILE {path}: keys {owner[digest]} and {n} list the same secret"
                )
            owner[digest] = n
            by_digest[digest] = key
    return KeyTable(by_digest)


class BearerGate:
    """ASGI middleware that serves each HTTP request as the key its bearer token resolves to.

    Raw ASGI (not BaseHTTPMiddleware) to avoid buffering MCP streaming.
    Non-HTTP scopes (lifespan) pass through.
    """

    def __init__(self, app: Any, keys: KeyTable | None):
        """With `keys` None (no keys file), every request is served as `LOCAL_KEY`."""
        self.app = app
        self.keys = keys

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self.keys is None:
            key = LOCAL_KEY
        else:
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
            if not provided.startswith("Bearer "):
                logger.warning(
                    "Rejected request: missing Bearer prefix (path=%s)", scope.get("path")
                )
                await self._send_401(send)
                return
            resolved = self.keys.resolve(provided[7:])
            if resolved is None:
                logger.warning("Rejected request: invalid token (path=%s)", scope.get("path"))
                await self._send_401(send)
                return
            key = resolved

        token = set_key(key)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_key(token)

    @staticmethod
    async def _send_401(send: Any) -> None:
        err = canonical_error("unauthorized", "Invalid or missing token")
        body = json.dumps(err).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
