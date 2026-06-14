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
        mem0_api_base="http://localhost:9999",
        mem0_api_key="test-key",
    )


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture(autouse=True)
def tenant_context():
    """Set and reset tenant contextvar per test. Prevents leakage."""
    token = set_tenant("test_user")
    yield
    reset_tenant(token)


USER_A = "alice"
USER_B = "bob"
