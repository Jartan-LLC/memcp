"""Which backends the conformance suite runs against, and how to build them.

A backend is a name plus a zero-argument factory. Built-ins come from this
repository; anything else is registered through MEMCP_CONFORMANCE_EXTRA, which is
what makes A1's "any MemoryBackend implementation" true for out-of-tree adapters.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module

from memcp.backend.base import MemoryBackend

ENV_BACKENDS = "MEMCP_CONFORMANCE_BACKENDS"
ENV_EXTRA = "MEMCP_CONFORMANCE_EXTRA"


@dataclass(frozen=True)
class BackendSpec:
    """A backend the suite can instantiate, or a reason it cannot."""

    name: str
    factory: Callable[[], MemoryBackend]
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None


def _in_memory_spec() -> BackendSpec:
    from memcp.backend.in_memory import InMemoryBackend

    return BackendSpec("in_memory", InMemoryBackend)


def _sqlite_spec() -> BackendSpec:
    """A throwaway file per run — the suite must not inherit a previous run's rows."""
    import tempfile
    from pathlib import Path

    from memcp.backend.sqlite import SqliteBackend

    def factory() -> MemoryBackend:
        directory = tempfile.mkdtemp(prefix="memcp-conformance-sqlite-")
        return SqliteBackend(Path(directory) / "memcp.sqlite3")

    return BackendSpec("sqlite", factory)


def _mem0_spec() -> BackendSpec:
    from memcp.backend.mem0 import Mem0Backend

    base = os.environ.get("MEM0_API_BASE")
    key = os.environ.get("MEM0_API_KEY")

    def factory() -> MemoryBackend:
        if not base or not key:  # pragma: no cover - guarded by unavailable_reason
            raise RuntimeError("MEM0_API_BASE and MEM0_API_KEY are required")
        return Mem0Backend(base, key)

    reason = None if base and key else "MEM0_API_BASE and MEM0_API_KEY are not set"
    return BackendSpec("mem0", factory, unavailable_reason=reason)


def _cognee_spec() -> BackendSpec:
    """A cognee server, if one is configured.

    The tenant secret is part of the address, not just the credential: it derives every
    tenant login, so a run with a different secret would be talking to different
    accounts on the same server. It is required rather than defaulted for that reason.
    """
    from memcp.backend.cognee import CogneeBackend

    base = os.environ.get("COGNEE_API_BASE")
    secret = os.environ.get("COGNEE_TENANT_SECRET")

    def factory() -> MemoryBackend:
        if not base or not secret:  # pragma: no cover - guarded by unavailable_reason
            raise RuntimeError("COGNEE_API_BASE and COGNEE_TENANT_SECRET are required")
        return CogneeBackend(base, secret, dataset=os.environ.get("COGNEE_DATASET", "memcp"))

    reason = None if base and secret else "COGNEE_API_BASE and COGNEE_TENANT_SECRET are not set"
    return BackendSpec("cognee", factory, unavailable_reason=reason)


def builtin_specs() -> list[BackendSpec]:
    return [_in_memory_spec(), _sqlite_spec(), _mem0_spec(), _cognee_spec()]


def extra_specs() -> list[BackendSpec]:
    """Parse MEMCP_CONFORMANCE_EXTRA="name=package.module:factory,..." entries."""
    raw = os.environ.get(ENV_EXTRA, "").strip()
    if not raw:
        return []
    specs: list[BackendSpec] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item or ":" not in item:
            raise ValueError(
                f"Invalid {ENV_EXTRA} entry {item!r}. "
                "Expected name=package.module:factory, comma-separated."
            )
        name, target = item.split("=", 1)
        module_path, _, attr = target.partition(":")
        try:
            factory = getattr(import_module(module_path), attr)
        except (ImportError, AttributeError) as e:
            specs.append(
                BackendSpec(name.strip(), _unimportable, unavailable_reason=f"{target}: {e}")
            )
            continue
        specs.append(BackendSpec(name.strip(), factory))
    return specs


def _unimportable() -> MemoryBackend:  # pragma: no cover - never called
    raise RuntimeError("factory could not be imported")


def all_specs() -> list[BackendSpec]:
    return builtin_specs() + extra_specs()


def selected_specs() -> list[BackendSpec]:
    """Specs to run, honouring MEMCP_CONFORMANCE_BACKENDS as a comma-separated filter."""
    specs = all_specs()
    wanted = os.environ.get(ENV_BACKENDS, "").strip()
    if not wanted:
        return specs
    names = [n.strip() for n in wanted.split(",") if n.strip()]
    by_name = {s.name: s for s in specs}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(
            f"Unknown backend(s) in {ENV_BACKENDS}: {unknown}. Known: {sorted(by_name)}"
        )
    return [by_name[n] for n in names]


def spec_by_name(name: str) -> BackendSpec:
    for spec in all_specs():
        if spec.name == name:
            return spec
    raise KeyError(name)
