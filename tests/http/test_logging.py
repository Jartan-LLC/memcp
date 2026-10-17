"""Every log record names the key and space of the request it came from, if any."""

from __future__ import annotations

import contextvars
import json
import logging

from memcp.http.logging import JsonFormatter, KeyContextFilter, PlainFormatter
from memcp.identity import reset_key, set_key
from tests.keys import make_key


def _record() -> logging.LogRecord:
    record = logging.LogRecord("memcp.test", logging.INFO, __file__, 1, "hello", None, None)
    assert KeyContextFilter().filter(record)
    return record


def _outside_a_request() -> logging.LogRecord:
    return contextvars.Context().run(_record)


def test_a_record_inside_a_request_names_its_key_and_space():
    token = set_key(make_key("team-notes", "reader", name="ci"))
    try:
        record = _record()
    finally:
        reset_key(token)
    payload = json.loads(JsonFormatter().format(record))
    assert (payload["key"], payload["space"]) == ("ci", "team-notes")
    assert "user_id" not in payload
    assert "key=ci space=team-notes hello" in PlainFormatter().format(record)


def test_a_record_outside_a_request_has_neither():
    record = _outside_a_request()
    payload = json.loads(JsonFormatter().format(record))
    assert (payload["key"], payload["space"]) == (None, None)
    assert "key=- space=- hello" in PlainFormatter().format(record)


def test_a_space_with_control_characters_cannot_forge_a_plain_line():
    token = set_key(make_key("team\nFAKE ERROR line"))
    try:
        record = _record()
    finally:
        reset_key(token)
    assert "\n" not in PlainFormatter().format(record)
