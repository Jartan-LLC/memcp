"""Tests for the request identity context."""

from __future__ import annotations

import contextvars

import pytest

from memcp.identity import current_key, reset_key, set_key
from tests.keys import make_key


def test_the_current_key_has_no_default():
    with pytest.raises(LookupError):
        contextvars.Context().run(current_key)


async def test_contextvar_isolation():
    """Verify contextvars don't leak between calls."""
    before = current_key()
    tok1 = set_key(make_key("space_one"))
    assert current_key() == make_key("space_one")
    reset_key(tok1)

    tok2 = set_key(make_key("space_two", "reader"))
    assert current_key() == make_key("space_two", "reader")
    reset_key(tok2)
    assert current_key() == before
