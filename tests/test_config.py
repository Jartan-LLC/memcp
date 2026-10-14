"""Server configuration that has no safe default."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from memcp.config import Config


def test_the_backend_must_be_chosen(monkeypatch: pytest.MonkeyPatch):
    """Without a default, a missing MEMCP_BACKEND fails and names itself."""
    monkeypatch.delenv("MEMCP_BACKEND", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Config.model_validate({"MEMCP_HOST": "127.0.0.1"})
    errors = {(e["type"], e["loc"]) for e in excinfo.value.errors()}
    assert ("missing", ("MEMCP_BACKEND",)) in errors
