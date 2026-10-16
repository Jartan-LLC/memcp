"""Shared test fixtures."""

from __future__ import annotations

import logging

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.config import Config
from memcp.identity import reset_key, set_key
from tests.keys import make_key


@pytest.fixture
def config() -> Config:
    """Minimal config for testing — no real backend needed.

    Loopback because it names no keys file: Config refuses that combination on any
    interface another machine can reach (SEC-2026-0059), and a test fixture should be a
    shape a deployment is allowed to have.
    """
    return Config(
        MEMCP_BACKEND="in_memory",
        MEMCP_HOST="127.0.0.1",
    )


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture(autouse=True)
def audit_at_info():
    """Let memcp.audit log at INFO, as setup_logging() does: migrate() refuses otherwise."""
    audit = logging.getLogger("memcp.audit")
    level = audit.level
    audit.setLevel(logging.INFO)
    yield
    audit.setLevel(level)


@pytest.fixture(autouse=True)
def key_context():
    """Act as a writer on `test_user` for each test, reset after it. Prevents leakage."""
    tok = set_key(make_key("test_user"))
    yield
    reset_key(tok)
