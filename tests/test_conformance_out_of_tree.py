"""The out-of-tree path, run the way an adapter author runs it.

A1 claims the suite runs against any `MemoryBackend`. Checking that from inside this
repository proves nothing: the repo's own `pyproject.toml` supplies pytest settings
and `memcp/conformance/portability.py` is editable here. Both were load-bearing and
neither is true for someone else's adapter, so this drives the documented recipe in a
subprocess from a scratch directory with its own pytest configuration.

Two defects this guards, both found by Corin verifying JAR-382:

1. Bare async fixtures needed `asyncio_mode = "auto"` from this repo's config, so out
   of tree every test errored with "requested an async fixture 'backend'".
2. A pair could only be declared by editing a file inside the installed package,
   so the round trip failed with UndocumentedPairError forever.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER = '''
"""A minimal out-of-tree adapter, written the way docs/conformance.md says to."""

from typing import Any

from memcp.backend.in_memory import InMemoryBackend
from memcp.conformance.portability import IDENTITY_LOSSES, declare_pair


class MyBackend(InMemoryBackend):
    """Storage borrowed from in_memory; capability set deliberately narrower."""

    def capabilities(self) -> set[str]:
        return {"get_memory", "update_memory", "list_memories"}

    # Narrowing by omission is not enough — an undeclared method has to refuse.
    async def history(self, user_id: str, memory_id: str) -> Any:
        raise NotImplementedError

    async def entities(self, user_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError


for _pair in (("mystore", "mystore"), ("mystore", "in_memory"), ("in_memory", "mystore")):
    declare_pair(*_pair, IDENTITY_LOSSES)
'''

# Nothing but a project table: no asyncio_mode, no testpaths, no plugins.
PYPROJECT = """
[project]
name = "adapter-probe"
version = "0.0.0"
"""


def _run(tmp_path: Path, *args: str, backends: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "my_adapter.py").write_text(ADAPTER, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMCP_", "MEM0_", "PYTEST_"))}
    env["PYTHONPATH"] = str(tmp_path)
    env["MEMCP_CONFORMANCE_EXTRA"] = "mystore=my_adapter:MyBackend"
    env["MEMCP_CONFORMANCE_BACKENDS"] = backends

    return subprocess.run(
        [sys.executable, "-m", "memcp.conformance", "-q", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.slow
def test_out_of_tree_adapter_passes_the_documented_command(tmp_path: Path):
    report_path = tmp_path / "report.json"
    result = _run(
        tmp_path,
        f"--conformance-report={report_path}",
        backends="mystore,in_memory",
    )
    assert result.returncode == 0, (
        "the documented out-of-tree command must pass against a conforming adapter.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "async fixture" not in result.stdout, (
        f"async fixtures broke without this repository's asyncio_mode setting:\n{result.stdout}"
    )
    assert "UndocumentedPairError" not in result.stdout, (
        f"declare_pair() did not register the pair:\n{result.stdout}"
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))

    mystore = report["backends"]["mystore"]
    assert mystore["available"] is True
    assert mystore["declared_capabilities"] == ["get_memory", "list_memories", "update_memory"]
    assert mystore["not_implemented"] == ["memory_entities", "memory_history"]

    statuses = {(r["backend"], r["capability"]): r["status"] for r in report["results"]}
    assert statuses[("mystore", "(required methods)")] == "PASS"
    assert statuses[("mystore", "get_memory")] == "PASS"
    assert statuses[("mystore", "round_trip")] == "PASS"
    # A2's distinction, seen from outside: undeclared reads as SKIP, not as a pass.
    assert statuses[("mystore", "memory_history")] == "SKIP"
    assert statuses[("mystore", "memory_entities")] == "SKIP"

    pairs = {(t["source"], t["target"]) for t in report["round_trips"] if t["ran"]}
    assert pairs == {
        ("mystore", "mystore"),
        ("mystore", "in_memory"),
        ("in_memory", "mystore"),
        ("in_memory", "in_memory"),
    }
    for trip in report["round_trips"]:
        assert trip["undocumented_losses"] == []
        assert trip["stale_declarations"] == []
        assert trip["exported"] == trip["imported"] == 24


@pytest.mark.slow
def test_out_of_tree_undeclared_pair_still_fails_loudly(tmp_path: Path):
    """The gate has to stay a gate: an undeclared pair fails, it does not skip."""
    adapter = ADAPTER.replace(
        'for _pair in (("mystore", "mystore"), ("mystore", "in_memory"), '
        '("in_memory", "mystore")):\n    declare_pair(*_pair, IDENTITY_LOSSES)',
        "# no declare_pair call at all",
    )
    (tmp_path / "my_adapter.py").write_text(adapter, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMCP_", "MEM0_", "PYTEST_"))}
    env["PYTHONPATH"] = str(tmp_path)
    env["MEMCP_CONFORMANCE_EXTRA"] = "mystore=my_adapter:MyBackend"
    env["MEMCP_CONFORMANCE_BACKENDS"] = "mystore"

    result = subprocess.run(
        [sys.executable, "-m", "memcp.conformance", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode != 0
    assert "UndocumentedPairError" in result.stdout
    assert "declare_pair" in result.stdout, (
        "the error must name the fix an out-of-tree author can actually apply, not a "
        f"file inside site-packages:\n{result.stdout}"
    )
