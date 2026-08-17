"""Fixtures for the conformance suite.

Self-contained on purpose: the suite runs from an installed memcp against an
adapter in someone else's repository, so it must not depend on this repository's
tests/conftest.py or on the caller's pytest configuration.

That independence is why nothing here relies on `asyncio_mode = "auto"`, which lives
in *this* repository's pyproject.toml and is absent from anyone else's:

- fixtures use `@pytest_asyncio.fixture` with an explicit loop scope, because a bare
  async fixture in strict mode fails with "requested an async fixture 'backend', with
  no plugin or hook that handled it";
- every suite module carries `pytestmark = pytest.mark.asyncio`, because pytest-asyncio
  decides how to run a test while collecting it. An earlier version added that marker
  from `pytest_collection_modifyitems`, which is too late to have any effect — out of
  tree it left every async test reported as "async def functions are not natively
  supported".
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from memcp.backend.base import MemoryBackend
from memcp.conformance.plugin import recorder
from memcp.conformance.registry import BackendSpec, selected_specs
from memcp.conformance.report import Recorder

PairSpec = tuple[BackendSpec, BackendSpec]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "backend_spec" in metafunc.fixturenames:
        specs = selected_specs()
        metafunc.parametrize("backend_spec", specs, ids=[s.name for s in specs])
    if "pair_spec" in metafunc.fixturenames:
        specs = selected_specs()
        pairs = [(src, dst) for src in specs for dst in specs]
        metafunc.parametrize("pair_spec", pairs, ids=[f"{s.name}-to-{d.name}" for s, d in pairs])


@pytest.fixture
def tenant() -> str:
    """A fresh tenant id per test — backends are shared, tenants are not."""
    return f"conf_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def other_tenant() -> str:
    return f"conf_other_{uuid.uuid4().hex[:12]}"


async def _open(spec: BackendSpec, config: pytest.Config) -> MemoryBackend:
    rec = recorder(config)
    if not spec.available:
        assert spec.unavailable_reason is not None
        rec.record_unavailable(spec.name, spec.unavailable_reason)
        pytest.skip(f"{spec.name}: {spec.unavailable_reason}")
    backend = spec.factory()
    rec.record_declared(spec.name, set(backend.capabilities()))
    return backend


async def _wipe(backend: MemoryBackend, *tenants: str) -> None:
    """delete_all with an empty scope removes everything for a tenant.

    The MCP tool refuses an empty scope; the backend method does not, which is what
    makes it usable as test teardown.
    """
    for tenant_id in tenants:
        # Teardown must never mask the failure that is already being reported.
        with contextlib.suppress(Exception):
            await backend.delete_all(tenant_id, {})


@pytest_asyncio.fixture(loop_scope="function")
async def backend(
    backend_spec: BackendSpec, tenant: str, other_tenant: str, request: pytest.FixtureRequest
) -> AsyncGenerator[MemoryBackend]:
    instance = await _open(backend_spec, request.config)
    try:
        yield instance
    finally:
        await _wipe(instance, tenant, other_tenant)
        await instance.close()


@pytest_asyncio.fixture(loop_scope="function")
async def pair(
    pair_spec: PairSpec, tenant: str, other_tenant: str, request: pytest.FixtureRequest
) -> AsyncGenerator[tuple[MemoryBackend, MemoryBackend]]:
    """Source and target backend instances for a migration.

    Always two instances, even when both are the same backend name — a same-name
    pair is the backup-and-restore case and still has to hold.
    """
    source_spec, target_spec = pair_spec
    source = await _open(source_spec, request.config)
    target = await _open(target_spec, request.config)
    try:
        yield source, target
    finally:
        await _wipe(source, tenant)
        await _wipe(target, other_tenant)
        await source.close()
        if target is not source:
            await target.close()


@pytest.fixture
def report(request: pytest.FixtureRequest) -> Recorder:
    """The run's Recorder, for tests that publish more than pass/fail."""
    return recorder(request.config)
