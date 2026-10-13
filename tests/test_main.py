"""`python -m memcp` is the Docker image's entrypoint: it must start the server."""

from __future__ import annotations

import pytest

from memcp import __main__ as entrypoint


def test_bare_invocation_serves(monkeypatch: pytest.MonkeyPatch):
    called: list[str] = []
    monkeypatch.setattr(entrypoint, "serve", lambda: called.append("serve"))
    entrypoint.main([])
    assert called == ["serve"]


def test_an_argument_is_an_error_not_a_silent_serve(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(entrypoint, "serve", lambda: pytest.fail("should not have served"))
    with pytest.raises(SystemExit) as excinfo:
        entrypoint.main(["--backend=sqlite"])
    assert excinfo.value.code == 2
