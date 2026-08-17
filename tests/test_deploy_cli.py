"""The command surface, including the part that must not have changed.

`python -m memcp` with no arguments still starts the server: that is the Docker
image's entrypoint and what every existing deployment runs, so a subcommand
dispatcher that swallowed it would be a breaking change wearing a feature's clothes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memcp import __main__ as entrypoint
from memcp.deploy import cli, runner


def test_bare_invocation_still_serves(monkeypatch: pytest.MonkeyPatch):
    called: list[str] = []
    monkeypatch.setattr(entrypoint, "serve", lambda: called.append("serve"))
    entrypoint.main([])
    assert called == ["serve"]


def test_serve_subcommand_serves(monkeypatch: pytest.MonkeyPatch):
    called: list[str] = []
    monkeypatch.setattr(entrypoint, "serve", lambda: called.append("serve"))
    entrypoint.main(["serve"])
    assert called == ["serve"]


def test_an_unknown_argument_is_an_error_not_a_silent_serve(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(entrypoint, "serve", lambda: pytest.fail("should not have served"))
    with pytest.raises(SystemExit) as excinfo:
        entrypoint.main(["--backend=sqlite"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("command", ["up", "down", "plan", "status", "rotate-token"])
def test_deploy_commands_route_to_the_deploy_cli(command: str, monkeypatch: pytest.MonkeyPatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 0)
    with pytest.raises(SystemExit) as excinfo:
        entrypoint.main([command])
    assert excinfo.value.code == 0
    assert seen == [[command]]


def test_plan_prints_without_creating_anything(tmp_path: Path, capsys: pytest.CaptureFixture):
    code = cli.main(["plan", "--dir", str(tmp_path), "--backend", "sqlite"])
    out = capsys.readouterr().out
    assert code == 0
    assert "CONTAINERS" in out
    assert list(tmp_path.iterdir()) == [], "plan created files"


def test_plan_json_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture):
    import json

    assert cli.main(["plan", "--json", "--dir", str(tmp_path), "--backend", "mem0"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in doc["containers"]] == ["postgres", "mem0", "memcp"]
    assert [p["service"] for p in doc["ports"]] == ["memcp"]
    assert any(s["name"] == "OPENAI_API_KEY" for s in doc["secrets"])


def test_plan_for_mem0_without_a_key_still_plans(tmp_path: Path, capsys: pytest.CaptureFixture):
    """`plan` is how you find out what you need — it must not require it first."""
    assert cli.main(["plan", "--dir", str(tmp_path), "--backend", "mem0"]) == 0
    assert "REQUIRED from you" in capsys.readouterr().out


def test_down_on_nothing_says_so(tmp_path: Path, capsys: pytest.CaptureFixture):
    assert cli.main(["down", "--dir", str(tmp_path)]) == 1
    assert "no deployment" in capsys.readouterr().err


def test_rotate_on_nothing_says_so(tmp_path: Path, capsys: pytest.CaptureFixture):
    assert cli.main(["rotate-token", "--dir", str(tmp_path)]) == 1
    assert "Run `memcp up` first" in capsys.readouterr().err


def test_client_snippet_carries_the_token_and_the_published_port():
    snippet = runner.client_snippet("tok-123", "127.0.0.1", 9001)
    assert '"Authorization": "Bearer tok-123"' in snippet
    assert "http://localhost:9001/mcp" in snippet
    assert '"type": "streamable-http"' in snippet


def test_source_resolution_prefers_the_checkout_it_runs_from(tmp_path: Path):
    source = runner.resolve_source("auto", tmp_path)
    assert source.generated is False
    assert source.dockerfile == "Dockerfile"


def test_pypi_source_generates_a_digest_pinned_dockerfile(tmp_path: Path):
    from memcp import __version__

    deployment, source, values, _ = runner.prepare(
        "sqlite", tmp_path, port=8080, bind="127.0.0.1", project="memcp", source_spec="pypi"
    )
    assert source.generated is True
    runner.materialize(deployment, tmp_path, source, values)
    dockerfile = (tmp_path / "memcp-image" / "Dockerfile").read_text()
    assert "@sha256:" in dockerfile
    assert f"memcp-server=={__version__}" in dockerfile
    assert "docker.sock" not in dockerfile


def test_a_source_path_without_a_dockerfile_is_refused(tmp_path: Path):
    with pytest.raises(runner.DeployError, match="no Dockerfile"):
        runner.resolve_source(str(tmp_path), tmp_path)
