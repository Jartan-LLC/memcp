"""Configuration — loaded from environment variables at startup, never at import time."""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from memcp.backend.cognee import DEFAULT_EMAIL_DOMAIN as COGNEE_DEFAULT_EMAIL_DOMAIN


def is_loopback(host: str) -> bool:
    """True when binding `host` cannot be reached from another machine.

    An empty host, a hostname, or `0.0.0.0` are all treated as non-loopback: what
    matters is whether we can *prove* the listener is local, not whether it probably
    is.
    """
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class Config(BaseSettings):
    """Server configuration. All values come from environment variables."""

    model_config = {"env_prefix": "", "populate_by_name": True}

    memcp_backend: Literal["mem0", "in_memory", "sqlite", "cognee"] = Field(
        default="mem0", alias="MEMCP_BACKEND"
    )

    # mem0 backend config (required when MEMCP_BACKEND=mem0)
    mem0_api_base: str | None = None
    mem0_api_key: str | None = None

    # sqlite backend config
    memcp_sqlite_path: str = Field(default="memcp.sqlite3", alias="MEMCP_SQLITE_PATH")

    # cognee backend config (required when MEMCP_BACKEND=cognee)
    cognee_api_base: str | None = None
    # Derives every tenant's cognee account. Two memcp processes serving the same
    # cognee server must hold the same value or they address different tenants.
    cognee_tenant_secret: str | None = None
    cognee_dataset: str = Field(default="memcp", alias="COGNEE_DATASET")
    cognee_email_domain: str = Field(
        default=COGNEE_DEFAULT_EMAIL_DOMAIN, alias="COGNEE_EMAIL_DOMAIN"
    )

    memcp_auth_tokens: str | None = Field(default=None, alias="MEMCP_AUTH_TOKENS")

    @field_validator("memcp_auth_tokens", mode="before")
    @classmethod
    def _empty_tokens_is_none(cls, v: str | None) -> str | None:
        return v if v else None

    host: str = Field(default="0.0.0.0", alias="MEMCP_HOST")
    port: int = Field(default=8080, alias="MEMCP_PORT")

    # Host header allow-list for DNS-rebinding protection, comma-separated. Entries
    # may end in `:*` to admit any port — `127.0.0.1:*`, `memory.example.com`.
    # Unset leaves the MCP SDK's own rule in place; see allowed_hosts_list.
    memcp_allowed_hosts: str | None = Field(default=None, alias="MEMCP_ALLOWED_HOSTS")

    @field_validator("memcp_allowed_hosts", mode="before")
    @classmethod
    def _empty_hosts_is_none(cls, v: str | None) -> str | None:
        return v if v else None

    @property
    def allowed_hosts_list(self) -> list[str] | None:
        """Explicit Host allow-list, or None to leave the SDK's default in place.

        What the SDK does on its own, established against mcp 2.0.0: passing
        `host="127.0.0.1"`, `"localhost"` or `"::1"` to `streamable_http_app` turns
        DNS-rebinding protection on with a loopback allow-list; **any other value,
        including `0.0.0.0`, leaves it off entirely** and no Host or Origin header is
        checked. Since `0.0.0.0` is memcp's default bind, protection is off by
        default — set this to turn it on with the names your deployment answers to.
        """
        if not self.memcp_allowed_hosts:
            return None
        return [h.strip() for h in self.memcp_allowed_hosts.split(",") if h.strip()]

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="MEMCP_LOG_LEVEL"
    )
    log_format: Literal["json", "plain"] = Field(default="json", alias="MEMCP_LOG_FORMAT")

    @model_validator(mode="after")
    def _validate_backend_config(self) -> Config:
        if self.memcp_backend == "mem0":
            if not self.mem0_api_base:
                raise ValueError("MEM0_API_BASE is required when MEMCP_BACKEND=mem0")
            if not self.mem0_api_key:
                raise ValueError("MEM0_API_KEY is required when MEMCP_BACKEND=mem0")
        if self.memcp_backend == "cognee":
            if not self.cognee_api_base:
                raise ValueError("COGNEE_API_BASE is required when MEMCP_BACKEND=cognee")
            if not self.cognee_tenant_secret:
                raise ValueError(
                    "COGNEE_TENANT_SECRET is required when MEMCP_BACKEND=cognee. It "
                    "derives each tenant's cognee account, so an unset secret would "
                    "put every tenant in the same one."
                )
        return self

    @model_validator(mode="after")
    def _refuse_unauthenticated_exposure(self) -> Config:
        """Refuse the one combination that has no safe reading: no token, public bind.

        memcp resolves tenant identity *from* the bearer token, so a server with no
        token configured has one tenant called `default_user` and no gate in front of
        it. On loopback that is a legitimate single-user dev server. On any other
        interface it is an open memory store on the network — SEC-2026-0059, and the
        shape SEC-2026-0038 already has in production.

        The default bind is deliberately left at 0.0.0.0: a container that listens on
        loopback publishes nothing, so flipping the default would break every existing
        deployment to fix a case that setting a token also fixes.
        """
        if not self.memcp_auth_tokens and not is_loopback(self.host):
            raise ValueError(
                f"Refusing to start: MEMCP_HOST={self.host} is reachable from other "
                "machines and MEMCP_AUTH_TOKENS is unset, so every request would be "
                "served as one unauthenticated tenant.\n"
                "  Set MEMCP_AUTH_TOKENS=<token>:<user_id> to serve it authenticated, "
                "or MEMCP_HOST=127.0.0.1 to keep it on this machine.\n"
                "  `memcp up` mints a token for you and does neither by accident."
            )
        return self

    @property
    def backend_name(self) -> str:
        return self.memcp_backend

    @property
    def version(self) -> str:
        from memcp import __version__

        return __version__
