"""The command surface, including the part that must not have changed.

`python -m memcp` with no arguments still starts the server: that is the Docker
image's entrypoint and what every existing deployment runs, so a subcommand
dispatcher that swallowed it would be a breaking change wearing a feature's clothes.
"""

from __future__ import annotations

import argparse
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


# ---------------------------------------------------------------------------
# plan and up describe the same deployment
# ---------------------------------------------------------------------------
#
# `_plan` once passed five of the six shaping flags and dropped --llm-base-url, so
# `plan --backend mem0 --llm-base-url ...` reported OPENAI_API_KEY as the operator's
# to supply and never mentioned OPENAI_BASE_URL, while `up` with the identical flags
# minted a placeholder and pointed mem0's LLM at that endpoint. The plan omitted an
# egress destination the deployment would configure, which is C6 failing rather than
# a cosmetic mismatch. These two tests close both directions: a flag that reaches one
# command and not the other, and a flag one parser accepts and the other does not.

SHAPING_FLAGS = [
    pytest.param([], id="defaults"),
    pytest.param(["--backend", "mem0"], id="mem0"),
    pytest.param(
        ["--backend", "mem0", "--llm-base-url", "http://localhost:11434/v1"],
        id="mem0-local-llm",
    ),
    pytest.param(["--backend", "sqlite", "--llm-base-url", "http://x:1/v1"], id="sqlite-llm-url"),
    pytest.param(["--backend", "in_memory"], id="in-memory"),
    pytest.param(["--port", "9999", "--bind", "0.0.0.0"], id="port-and-bind"),
    pytest.param(["--memcp-source", "pypi"], id="pypi-source"),
    pytest.param(
        ["--no-publish", "--external-url", "https://memory.example.com"], id="unpublished"
    ),
    pytest.param(
        [
            "--no-publish",
            "--network",
            "dokploy-network",
            "--external-url",
            "https://memory.example.com",
        ],
        id="unpublished-external-network",
    ),
    pytest.param(["--external-url", "https://memory.example.com"], id="published-behind-a-proxy"),
    pytest.param(
        [
            "--backend",
            "mem0",
            "--llm-base-url",
            "http://gpu.lan:8000/v1",
            "--port",
            "7000",
            "--bind",
            "0.0.0.0",
            "--memcp-source",
            "pypi",
            "--project",
            "second",
        ],
        id="everything-at-once",
    ),
]


def _captured_call(
    command: str, flags: list[str], directory: Path, monkeypatch: pytest.MonkeyPatch
) -> dict:
    """What the command actually asks the runner to build.

    Intercepting `plan_for`/`prepare` rather than calling the helper both commands
    happen to share: the defect this guards against is a call site dropping an
    argument, so a test that routes through the shared helper cannot see it. Confirmed
    by reintroducing the bug — this catches it and a helper-level check did not.
    """
    seen: dict = {}

    def capture(backend: str, _directory: Path, **kwargs):
        seen.update(backend=backend, **kwargs)
        raise runner.DeployError("captured; nothing was created")

    monkeypatch.setattr(runner, "plan_for", capture)
    monkeypatch.setattr(runner, "prepare", capture)
    monkeypatch.setattr(runner, "require_docker", lambda: None)
    cli.main([command, "--dir", str(directory), *flags])
    assert seen, f"`{command}` never reached the runner"
    return seen


@pytest.mark.parametrize("flags", SHAPING_FLAGS)
def test_plan_and_up_ask_for_the_same_deployment(
    flags: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same flags in, same request out — every shaping argument, from both commands."""
    planned = _captured_call("plan", flags, tmp_path, monkeypatch)
    provisioned = _captured_call("up", flags, tmp_path, monkeypatch)
    assert planned == provisioned


@pytest.mark.parametrize("flags", SHAPING_FLAGS)
def test_plan_and_up_agree_on_the_secrets_list(
    flags: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Called out separately because this is what an operator reads before running up.

    Whether a secret is minted or required from you is the difference between "memcp
    handles this" and "go get an account", and the plan is where that is answered.
    """
    planned = _captured_call("plan", flags, tmp_path, monkeypatch)
    provisioned = _captured_call("up", flags, tmp_path, monkeypatch)
    monkeypatch.undo()

    def secrets(captured: dict) -> list[tuple[str, bool]]:
        backend = captured.pop("backend")
        deployment, _ = runner.plan_for(backend, tmp_path, **captured)
        return [(s.name, s.minted) for s in deployment.secrets]

    assert secrets(planned) == secrets(provisioned)


def test_plan_and_up_accept_the_same_shaping_flags():
    """A flag added to one parser and not the other is the same bug, one step earlier."""
    parser = cli.build_parser()
    subparsers = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    plan_only = {"--json"}
    up_only = {"--timeout", "--smoke"}

    def options(name: str) -> set[str]:
        return {option for action in subparsers[name]._actions for option in action.option_strings}

    assert options("plan") - plan_only == options("up") - up_only


def test_the_local_llm_endpoint_reaches_the_plan(tmp_path: Path, capsys: pytest.CaptureFixture):
    """The original defect, end to end through the command rather than the helper."""
    assert (
        cli.main(
            [
                "plan",
                "--dir",
                str(tmp_path),
                "--backend",
                "mem0",
                "--llm-base-url",
                "http://localhost:11434/v1",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "OPENAI_BASE_URL=http://localhost:11434/v1" in out, "plan hid an egress destination"
    assert "REQUIRED from you" not in out, "plan still demands a key up would have minted"
    assert "minted by memcp" in out


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


def test_client_snippet_carries_the_token_and_the_url_the_deployment_answers_on():
    snippet = runner.client_snippet("tok-123", "http://localhost:9001/mcp")
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
