"""The audit log: one record per policy operation, naming the key and never the content."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

import pytest

from memcp.backends.capabilities import ALL_CAPABILITIES
from memcp.backends.in_memory import InMemoryBackend
from memcp.http.logging import JsonFormatter, PlainFormatter
from memcp.identity import reset_key, set_key
from memcp.policy import Policy
from memcp.policy.transfer import migrate
from memcp.types import EntitiesResult, MemoryAPIError, PolicyError
from tests.keys import make_key
from tests.policy.test_access import CALLS, MISSING_ID, WRITES

CONTENT = "okapi-content-marker"
QUERY = "okapi-query-marker"
METADATA = {"note": "okapi-metadata-marker"}
MARKERS = ("okapi-content-marker", "okapi-query-marker", "okapi-metadata-marker")
BY_ID = {"get", "update", "delete", "history"}


class _Everything(InMemoryBackend):
    """in_memory, declaring every capability, with an empty entity graph."""

    def capabilities(self) -> set[str]:
        return set(ALL_CAPABILITIES)

    async def entities(self, partition: str, **kwargs: Any) -> EntitiesResult:
        return EntitiesResult()


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "memcp.audit"]


async def _run(
    policy: Policy, call: Any, caplog: pytest.LogCaptureFixture, relation: str = "writer"
) -> list[logging.LogRecord]:
    token = set_key(make_key("team", relation, name="laptop"))  # type: ignore[arg-type]
    try:
        with caplog.at_level(logging.INFO, logger="memcp.audit"), suppress(PolicyError):
            await call(policy)
    finally:
        reset_key(token)
    return _records(caplog)


@pytest.mark.parametrize("operation", sorted(CALLS))
async def test_every_operation_writes_exactly_one_record(
    operation: str, caplog: pytest.LogCaptureFixture
):
    records = await _run(Policy(_Everything()), CALLS[operation], caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    audit = records[0].audit  # type: ignore[attr-defined]
    assert (audit["operation"], audit["key"], audit["space"], audit["relation"]) == (
        operation,
        "laptop",
        "team",
        "writer",
    )
    assert audit["outcome"] == ("not_found" if operation in BY_ID else "ok")
    if operation in BY_ID:
        assert audit["ids"] == [MISSING_ID]


@pytest.mark.parametrize("operation", sorted(WRITES))
async def test_a_forbidden_write_is_recorded_as_forbidden(
    operation: str, caplog: pytest.LogCaptureFixture
):
    records = await _run(Policy(_Everything()), CALLS[operation], caplog, relation="reader")
    assert [r.audit["outcome"] for r in records] == ["forbidden"]  # type: ignore[attr-defined]
    assert records[0].audit["relation"] == "reader"  # type: ignore[attr-defined]


async def test_a_refused_argument_is_recorded_by_its_code(caplog: pytest.LogCaptureFixture):
    records = await _run(Policy(_Everything()), lambda p: p.add(""), caplog)
    assert [r.audit["outcome"] for r in records] == ["validation_error"]  # type: ignore[attr-defined]


async def test_a_refused_by_id_call_records_its_valid_id(caplog: pytest.LogCaptureFixture):
    policy = Policy(_Everything())
    forbidden = await _run(policy, lambda p: p.delete(MISSING_ID), caplog, relation="reader")
    caplog.clear()
    refused = await _run(policy, lambda p: p.update(MISSING_ID), caplog)
    assert [(r.audit["outcome"], r.audit["ids"]) for r in forbidden + refused] == [  # type: ignore[attr-defined]
        ("forbidden", [MISSING_ID]),
        ("validation_error", [MISSING_ID]),
    ]


@pytest.mark.parametrize(
    ("raised", "outcome"),
    [
        (MemoryAPIError(500, "store said no"), "backend_error"),
        (MemoryAPIError(408, "store said no"), "timeout"),
        (asyncio.CancelledError(), "cancelled"),
        (RuntimeError("bug"), "error"),
    ],
    ids=["5xx", "timeout", "cancelled", "unexpected"],
)
async def test_a_failed_call_is_recorded_and_still_raised(
    raised: BaseException,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    backend = _Everything()

    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise raised

    monkeypatch.setattr(backend, "search", fail)
    token = set_key(make_key("team"))
    try:
        with caplog.at_level(logging.INFO, logger="memcp.audit"), pytest.raises(type(raised)):
            await Policy(backend).search("anything")
    finally:
        reset_key(token)
    assert [r.audit["outcome"] for r in _records(caplog)] == [outcome]  # type: ignore[attr-defined]


# --- what a record holds ---------------------------------------------------------------


async def test_records_name_ids_scope_and_counts(caplog: pytest.LogCaptureFixture):
    policy = Policy(_Everything())
    token = set_key(make_key("team", name="laptop"))
    try:
        await policy.add("before", scope={"agent_id": "b"}, infer=False)
        with caplog.at_level(logging.INFO, logger="memcp.audit"):
            added = await policy.add("a fact", scope={"agent_id": "a"}, infer=False)
            memory_id = added[0].id  # type: ignore[index]
            await policy.get(memory_id)
            await policy.search("fact", scope={"agent_id": "a"})
            await policy.list_memories(scope={"agent_id": "a"})
            await policy.export_memories()
            await policy.entities(scope={"agent_id": "a"})
            await policy.import_memories([{"content": "three"}, {"content": ""}])
            await policy.delete_all({"agent_id": "a"})
    finally:
        reset_key(token)
    audits = {r.audit["operation"]: r.audit for r in _records(caplog)}  # type: ignore[attr-defined]
    a = {"agent_id": "a"}
    assert (audits["add"]["ids"], audits["add"]["scope"]) == ([memory_id], a)
    assert audits["get"]["ids"] == [memory_id]
    assert (audits["search"]["count"], audits["search"]["scope"]) == (1, a)
    assert (audits["list_memories"]["count"], audits["list_memories"]["scope"]) == (1, a)
    assert audits["export_memories"]["count"] == 2
    assert (audits["entities"]["count"], audits["entities"]["scope"]) == (0, a)
    assert audits["import_memories"]["count"] == 1  # imported, not sent: one is refused
    assert (audits["delete_all"]["count"], audits["delete_all"]["scope"]) == (1, a)


async def test_no_record_carries_content_a_query_or_metadata(caplog: pytest.LogCaptureFixture):
    policy = Policy(_Everything())
    token = set_key(make_key("team"))
    try:
        with caplog.at_level(logging.INFO, logger="memcp.audit"):
            added = await policy.add(CONTENT, metadata=METADATA, infer=False)
            memory_id = added[0].id  # type: ignore[index]
            await policy.update(memory_id, CONTENT + " changed", metadata=METADATA)
            await policy.search(QUERY)
            await policy.import_memories([{"content": CONTENT, "metadata": METADATA}])
            await policy.export_memories()
    finally:
        reset_key(token)
    records = _records(caplog)
    assert len(records) == 5
    for record in records:
        for text in (JsonFormatter().format(record), PlainFormatter().format(record)):
            assert not any(marker in text for marker in MARKERS), text


@pytest.mark.parametrize("operation", sorted(BY_ID))
async def test_a_malformed_memory_id_is_not_recorded(
    operation: str, caplog: pytest.LogCaptureFixture
):
    """A client can put anything, content included, where a memory id belongs."""
    call = getattr(Policy, operation)
    malformed = f"{CONTENT} with spaces"
    records = await _run(Policy(_Everything()), lambda p: call(p, malformed), caplog)
    assert [r.audit["outcome"] for r in records] == ["validation_error"]  # type: ignore[attr-defined]
    assert records[0].audit["ids"] is None  # type: ignore[attr-defined]
    assert CONTENT not in JsonFormatter().format(records[0])


async def test_every_formatter_shows_the_actor(caplog: pytest.LogCaptureFixture):
    """memcp's JSON and plain formats, and a bare `%(message)s` as basicConfig gives."""
    records = await _run(Policy(_Everything()), lambda p: p.get(MISSING_ID), caplog)
    payload = json.loads(JsonFormatter().format(records[0]))
    assert payload["logger"] == "memcp.audit"
    assert payload["audit"] == records[0].audit  # type: ignore[attr-defined]
    assert (payload["key"], payload["space"]) == ("laptop", "team")
    for text in (PlainFormatter().format(records[0]), records[0].getMessage()):
        for field in ("operation=get", "outcome=not_found", "key=laptop", "space=team"):
            assert field in text, text


# --- migrate -------------------------------------------------------------------------


async def test_a_migration_is_recorded_under_its_operator(caplog: pytest.LogCaptureFixture):
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add("team", CONTENT, infer=False)
    await source.add("team", "already there", infer=False)
    await target.add("moved", "already there", infer=False)
    with caplog.at_level(logging.INFO, logger="memcp.audit"):
        report = await migrate(source, target, "team", target_partition="moved", operator="ops")
    assert (report.exported, report.skipped) == (2, 1)
    records = _records(caplog)
    assert len(records) == 1
    audit = records[0].audit  # type: ignore[attr-defined]
    assert (audit["operation"], audit["key"], audit["space"], audit["source_space"]) == (
        "migrate",
        "ops",
        "moved",
        "team",
    )
    assert (audit["relation"], audit["outcome"]) == (None, "ok")
    assert audit["count"] == report.imported == 1
    line = json.loads(JsonFormatter().format(records[0]))
    assert (line["key"], line["space"]) == ("ops", "moved")
    for text in (PlainFormatter().format(records[0]), records[0].getMessage()):
        assert "key=ops space=moved" in text, text
    assert CONTENT not in json.dumps(line)


async def test_a_refused_migration_is_recorded(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="memcp.audit"), pytest.raises(ValueError):
        await migrate(InMemoryBackend(), InMemoryBackend(), "*", operator="ops")
    assert [r.audit["outcome"] for r in _records(caplog)] == ["validation_error"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("operator", ["", "  "])
async def test_a_migration_needs_an_operator(operator: str):
    with pytest.raises(ValueError, match="operator"):
        await migrate(InMemoryBackend(), InMemoryBackend(), "team", operator=operator)


class _Unreadable(InMemoryBackend):
    async def list_memories(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("migrate read the source")


def _only_handler(
    audit: logging.Logger, monkeypatch: pytest.MonkeyPatch, handler: logging.Handler | None
) -> None:
    monkeypatch.setattr(audit, "propagate", False)
    monkeypatch.setattr(audit, "handlers", [] if handler is None else [handler])


def _handler(cls: type[logging.Handler], level: int) -> logging.Handler:
    handler = cls()
    handler.setLevel(level)
    return handler


@pytest.mark.parametrize(
    "cause", ["level", "no handler", "warning handler", "null handler", "disabled"]
)
async def test_a_migration_whose_record_would_be_lost_is_refused(
    cause: str, monkeypatch: pytest.MonkeyPatch
):
    audit = logging.getLogger("memcp.audit")
    if cause == "level":
        monkeypatch.setattr(audit, "level", logging.WARNING)
    elif cause == "disabled":
        monkeypatch.setattr(audit, "disabled", True)
    elif cause == "no handler":
        _only_handler(audit, monkeypatch, None)
    elif cause == "warning handler":
        _only_handler(audit, monkeypatch, _handler(logging.StreamHandler, logging.WARNING))
    else:
        _only_handler(audit, monkeypatch, _handler(logging.NullHandler, logging.NOTSET))
    with pytest.raises(ValueError, match="setup_logging"):
        await migrate(_Unreadable(), InMemoryBackend(), "team", operator="ops")


async def test_a_migration_runs_with_a_handler_at_exactly_info(monkeypatch: pytest.MonkeyPatch):
    audit = logging.getLogger("memcp.audit")
    _only_handler(audit, monkeypatch, _handler(logging.StreamHandler, logging.INFO))
    report = await migrate(InMemoryBackend(), InMemoryBackend(), "team", operator="ops")
    assert report.exported == 0


# --- setup -----------------------------------------------------------------------------


def test_the_audit_logger_stays_at_info_and_doesnt_propagate(monkeypatch: pytest.MonkeyPatch):
    """At INFO whatever MEMCP_LOG_LEVEL says; not propagating, so each record prints once."""
    from memcp.http import logging as http_logging

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(http_logging.logging.config, "dictConfig", seen.append)
    http_logging.setup_logging(level="WARNING")
    audit = seen[0]["loggers"]["memcp.audit"]
    assert (audit["level"], audit["propagate"]) == ("INFO", False)
    assert seen[0]["loggers"]["memcp"]["level"] == "WARNING"
