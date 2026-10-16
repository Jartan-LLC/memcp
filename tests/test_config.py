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


@pytest.mark.parametrize("variable", ["MEMCP_AUTH_TOKENS", "MEMCP_AUTH_SEATS"])
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_a_variable_the_keys_file_replaced_is_refused(variable: str, host: str):
    with pytest.raises(ValidationError) as excinfo:
        Config.model_validate(
            {"MEMCP_BACKEND": "in_memory", "MEMCP_HOST": host, variable: "tok:alice"}
        )
    message = str(excinfo.value)
    assert f"{variable} is set" in message
    assert "MEMCP_KEYS_FILE" in message
    assert "tok:alice" not in message


def test_a_replaced_variable_left_empty_counts_as_unset():
    config = Config.model_validate(
        {
            "MEMCP_BACKEND": "in_memory",
            "MEMCP_HOST": "127.0.0.1",
            "MEMCP_AUTH_TOKENS": "",
            "MEMCP_AUTH_SEATS": "",
        }
    )
    assert config.memcp_keys_file is None


def test_an_empty_keys_file_setting_counts_as_unset():
    config = Config.model_validate(
        {"MEMCP_BACKEND": "in_memory", "MEMCP_HOST": "127.0.0.1", "MEMCP_KEYS_FILE": ""}
    )
    assert config.memcp_keys_file is None
