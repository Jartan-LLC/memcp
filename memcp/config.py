"""Configuration — loaded from environment variables at startup, never at import time."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Server configuration. All values come from environment variables."""

    model_config = {"env_prefix": "", "populate_by_name": True}

    mem0_api_base: str
    mem0_api_key: str

    memcp_auth_tokens: str | None = Field(None, alias="MEMCP_AUTH_TOKENS")

    @field_validator("memcp_auth_tokens", mode="before")
    @classmethod
    def _empty_tokens_is_none(cls, v: str | None) -> str | None:
        return v if v else None

    host: str = Field("0.0.0.0", alias="MEMCP_HOST")
    port: int = Field(8080, alias="MEMCP_PORT")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", alias="MEMCP_LOG_LEVEL"
    )
    log_format: Literal["json", "plain"] = Field("json", alias="MEMCP_LOG_FORMAT")

    @property
    def backend_name(self) -> str:
        return "mem0"

    @property
    def version(self) -> str:
        from memcp import __version__

        return __version__
