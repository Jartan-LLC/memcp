"""pytest plugin that turns suite outcomes into the conformance report.

Registered through the `pytest11` entry point, so it loads wherever memcp is
installed — including `pytest --pyargs memcp.conformance.suite` from another
repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from memcp.conformance.report import FAIL, PASS, REQUIRED_GROUP, SKIP, Recorder

RECORDER_KEY = "_memcp_conformance_recorder"
_INDEX_KEY = "_memcp_conformance_index"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("memcp conformance")
    group.addoption(
        "--conformance-report",
        action="store",
        default=None,
        metavar="PATH",
        help="write the memcp backend conformance report as JSON to PATH",
    )


# pytest_runtest_logreport takes no config argument, so the active config is
# stashed here at configure time.
_CONFIG: dict[str, Any] = {}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "conformance(capability): mark a conformance test as covering one capability",
    )
    setattr(config, RECORDER_KEY, Recorder())
    setattr(config, _INDEX_KEY, {})
    _CONFIG["config"] = config


def recorder(config: pytest.Config) -> Recorder:
    rec = getattr(config, RECORDER_KEY, None)
    if rec is None:  # pragma: no cover - configure always runs first
        rec = Recorder()
        setattr(config, RECORDER_KEY, rec)
    return rec


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Index conformance items by nodeid so log reports can be attributed."""
    index: dict[str, tuple[str, str]] = getattr(config, _INDEX_KEY, {})
    for item in items:
        params = getattr(getattr(item, "callspec", None), "params", {})
        spec = params.get("backend_spec")
        pair = params.get("pair_spec")
        if spec is not None:
            backend = spec.name
        elif pair is not None:
            backend = pair[0].name
        else:
            continue
        marker = item.get_closest_marker("conformance")
        capability = str(marker.args[0]) if marker and marker.args else REQUIRED_GROUP
        index[item.nodeid] = (backend, capability)
    setattr(config, _INDEX_KEY, index)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    config = _CONFIG.get("config")
    if config is None:  # pragma: no cover - configure always runs first
        return
    index: dict[str, tuple[str, str]] = getattr(config, _INDEX_KEY, {})
    entry = index.get(report.nodeid)
    if entry is None:
        return
    backend, capability = entry
    rec = recorder(config)
    if report.failed:
        rec.record(backend, capability, FAIL)
    elif report.skipped:
        note = ""
        if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
            note = str(report.longrepr[2]).removeprefix("Skipped: ")
        rec.record(backend, capability, SKIP, note)
    elif report.when == "call":
        rec.record(backend, capability, PASS)


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    rec = recorder(config)
    if not rec.outcomes and not rec.round_trips:
        return
    terminalreporter.write_sep("=", "memcp conformance report")
    for line in rec.render_text():
        terminalreporter.write_line(line)
    path = config.getoption("--conformance-report")
    if path:
        target = Path(str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rec.to_dict(), indent=2, sort_keys=True) + "\n")
        terminalreporter.write_line(f"conformance report written to {target}")
