"""Configuration — loaded from environment variables at startup, never at import time."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Server configuration. All values come from environment variables."""

    model_config = {"env_prefix": "", "populate_by_name": True}

    memcp_backend: Literal["mem0", "in_memory"] = Field(default="mem0", alias="MEMCP_BACKEND")

    # mem0 backend config (required when MEMCP_BACKEND=mem0)
    mem0_api_base: str | None = None
    mem0_api_key: str | None = None

    memcp_auth_tokens: str | None = Field(default=None, alias="MEMCP_AUTH_TOKENS")

    @field_validator("memcp_auth_tokens", mode="before")
    @classmethod
    def _empty_tokens_is_none(cls, v: str | None) -> str | None:
        return v if v else None

    host: str = Field(default="0.0.0.0", alias="MEMCP_HOST")
    port: int = Field(default=8080, alias="MEMCP_PORT")

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
        return self

    @property
    def backend_name(self) -> str:
        return self.memcp_backend

    @property
    def version(self) -> str:
        from memcp import __version__

        return __version__
