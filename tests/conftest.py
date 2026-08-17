"""Shared test fixtures."""

from __future__ import annotations

import pytest

from memcp.auth import reset_tenant, set_tenant
from memcp.backend.in_memory import InMemoryBackend
from memcp.config import Config


@pytest.fixture
def config() -> Config:
    """Minimal config for testing — no real backend needed."""
    return Config(
        MEMCP_BACKEND="in_memory",
    )


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture(autouse=True)
def tenant_context():
    """Set and reset tenant contextvar per test. Prevents leakage."""
    tok = set_tenant("test_user")
    yield
    reset_tenant(tok)
