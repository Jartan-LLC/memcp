"""Fixtures that run the conformance suite against every backend this repository has.

in_memory and sqlite always run; mem0 runs when MEM0_API_BASE and MEM0_API_KEY point at
a server (tests/mem0 stands one up).
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from memcp.backends.base import MemoryBackend
from memcp.backends.in_memory import InMemoryBackend
from memcp.backends.mem0 import Mem0Backend
from memcp.backends.sqlite import SqliteBackend

BACKENDS = ("in_memory", "sqlite", "mem0")
PAIRS = [(source, target) for source in BACKENDS for target in BACKENDS]


def _skip_unavailable(*names: str) -> None:
    if "mem0" in names and not (
        os.environ.get("MEM0_API_BASE") and os.environ.get("MEM0_API_KEY")
    ):
        pytest.skip("mem0: MEM0_API_BASE and MEM0_API_KEY are not set")


def _open(name: str, directory: Path) -> MemoryBackend:
    if name == "in_memory":
        return InMemoryBackend()
    if name == "sqlite":
        return SqliteBackend(directory / "memcp.sqlite3")
    if name == "mem0":
        return Mem0Backend(os.environ["MEM0_API_BASE"], os.environ["MEM0_API_KEY"])
    raise ValueError(f"no conformance factory for backend {name!r}")


async def _wipe(backend: MemoryBackend, *tenants: str) -> None:
    """Empty each tenant. The backend method takes an empty scope; the MCP tool does not."""
    for tenant_id in tenants:
        # Teardown must never mask the failure that is already being reported.
        with contextlib.suppress(Exception):
            await backend.delete_all(tenant_id, {})


@pytest.fixture
def tenant() -> str:
    """A fresh tenant id per test: mem0 is shared across tests, tenants are not."""
    return f"conf_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def other_tenant() -> str:
    return f"conf_other_{uuid.uuid4().hex[:12]}"


@pytest.fixture(params=BACKENDS)
def backend_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def backend(
    backend_name: str, tenant: str, other_tenant: str, tmp_path: Path
) -> AsyncGenerator[MemoryBackend]:
    _skip_unavailable(backend_name)
    instance = _open(backend_name, tmp_path)
    try:
        yield instance
    finally:
        await _wipe(instance, tenant, other_tenant)
        await instance.close()


@pytest.fixture(params=PAIRS, ids=[f"{s}-to-{t}" for s, t in PAIRS])
def pair_names(request: pytest.FixtureRequest) -> tuple[str, str]:
    return request.param


@pytest.fixture
async def pair(
    pair_names: tuple[str, str], tenant: str, other_tenant: str, tmp_path: Path
) -> AsyncGenerator[tuple[MemoryBackend, MemoryBackend]]:
    """Source and target instances for a migration: two, even for a same-backend pair."""
    source_name, target_name = pair_names
    _skip_unavailable(source_name, target_name)
    source = _open(source_name, tmp_path / "source")
    try:
        target = _open(target_name, tmp_path / "target")
        try:
            yield source, target
        finally:
            await _wipe(target, other_tenant)
            await target.close()
    finally:
        await _wipe(source, tenant)
        await source.close()
