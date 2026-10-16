"""The audit log: one record per policy operation, naming the key and never the content."""

from __future__ import annotations

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


@pytest.mark.parametrize(("status", "outcome"), [(500, "backend_error"), (408, "timeout")])
async def test_a_backend_failure_is_recorded_and_still_raised(
    status: int, outcome: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    backend = _Everything()

    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise MemoryAPIError(status, "store said no")

    monkeypatch.setattr(backend, "search", fail)
    token = set_key(make_key("team"))
    try:
        with caplog.at_level(logging.INFO, logger="memcp.audit"), pytest.raises(MemoryAPIError):
            await Policy(backend).search("anything")
    finally:
        reset_key(token)
    assert [r.audit["outcome"] for r in _records(caplog)] == [outcome]  # type: ignore[attr-defined]


async def test_records_name_ids_scope_and_counts(caplog: pytest.LogCaptureFixture):
    policy = Policy(_Everything())
    token = set_key(make_key("team", name="laptop"))
    try:
        with caplog.at_level(logging.INFO, logger="memcp.audit"):
            added = await policy.add("a fact", scope={"agent_id": "a"}, infer=False)
            memory_id = added[0].id  # type: ignore[index]
            await policy.get(memory_id)
            await policy.search("fact", scope={"agent_id": "a"})
            await policy.delete_all({"agent_id": "a"})
    finally:
        reset_key(token)
    audits = [r.audit for r in _records(caplog)]  # type: ignore[attr-defined]
    assert [a["operation"] for a in audits] == ["add", "get", "search", "delete_all"]
    assert audits[0]["ids"] == [memory_id]
    assert audits[0]["scope"] == {"agent_id": "a"}
    assert audits[1]["ids"] == [memory_id]
    assert (audits[2]["count"], audits[2]["scope"]) == (1, {"agent_id": "a"})
    assert (audits[3]["count"], audits[3]["scope"]) == (1, {"agent_id": "a"})


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


async def test_the_json_line_carries_the_audit_object_and_plain_text_its_fields(
    caplog: pytest.LogCaptureFixture,
):
    records = await _run(Policy(_Everything()), lambda p: p.get(MISSING_ID), caplog)
    payload = json.loads(JsonFormatter().format(records[0]))
    assert payload["logger"] == "memcp.audit"
    assert payload["audit"] == records[0].audit  # type: ignore[attr-defined]
    plain = PlainFormatter().format(records[0])
    for field in ("operation=get", "outcome=not_found", "key=laptop", "space=team"):
        assert field in plain


# --- migrate -------------------------------------------------------------------------


async def test_a_migration_is_recorded_under_its_operator(caplog: pytest.LogCaptureFixture):
    source, target = InMemoryBackend(), InMemoryBackend()
    await source.add("team", CONTENT, infer=False)
    with caplog.at_level(logging.INFO, logger="memcp.audit"):
        report = await migrate(source, target, "team", target_partition="moved", operator="ops")
    audits = [r.audit for r in _records(caplog)]  # type: ignore[attr-defined]
    assert len(audits) == 1
    assert (audits[0]["operation"], audits[0]["key"], audits[0]["space"]) == (
        "migrate",
        "ops",
        "moved",
    )
    assert (audits[0]["relation"], audits[0]["outcome"]) == (None, "ok")
    assert audits[0]["count"] == report.imported == 1
    assert CONTENT not in JsonFormatter().format(_records(caplog)[0])


async def test_a_refused_migration_is_recorded(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO, logger="memcp.audit"), pytest.raises(ValueError):
        await migrate(InMemoryBackend(), InMemoryBackend(), "*", operator="ops")
    assert [r.audit["outcome"] for r in _records(caplog)] == ["validation_error"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("operator", ["", "  "])
async def test_a_migration_needs_an_operator(operator: str):
    with pytest.raises(ValueError, match="operator"):
        await migrate(InMemoryBackend(), InMemoryBackend(), "team", operator=operator)


# --- setup -----------------------------------------------------------------------------


def test_the_audit_logger_stays_at_info_whatever_the_log_level(monkeypatch: pytest.MonkeyPatch):
    from memcp.http import logging as http_logging

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(http_logging.logging.config, "dictConfig", seen.append)
    http_logging.setup_logging(level="WARNING")
    assert seen[0]["loggers"]["memcp.audit"]["level"] == "INFO"
    assert seen[0]["loggers"]["memcp"]["level"] == "WARNING"
