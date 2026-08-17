"""Shared test fixtures."""

from __future__ import annotations

import pytest

from memcp.auth import reset_tenant, set_tenant
from memcp.backend.in_memory import InMemoryBackend
from memcp.config import Config


@pytest.fixture
def config() -> Config:
    """Minimal config for testing — no real backend needed.

    Loopback because it carries no auth tokens: Config refuses that combination on
    any interface another machine can reach (SEC-2026-0059), and a test fixture
    should be a shape a deployment is allowed to have.
    """
    return Config(
        MEMCP_BACKEND="in_memory",
        MEMCP_HOST="127.0.0.1",
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
