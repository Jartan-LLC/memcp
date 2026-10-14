"""Tests for the request identity context."""

from __future__ import annotations

from memcp.identity import get_tenant, reset_tenant, set_tenant


async def test_contextvar_isolation():
    """Verify contextvars don't leak between calls."""
    tok1 = set_tenant("user_one")
    assert get_tenant() == "user_one"
    reset_tenant(tok1)

    tok2 = set_tenant("user_two")
    assert get_tenant() == "user_two"
    reset_tenant(tok2)
