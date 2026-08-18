"""A deployment behind a platform that routes into the container (JAR-559).

Dokploy owns the external port mapping and routes to the container itself, and the
same shape holds for Coolify, Kubernetes, and a hand-run Traefik or Caddy. Under all
of them a published host port is redundant at best and a second, unrouted way in at
worst — so `memcp up --no-publish` provisions a stack with no host port at all.

The default does not move: publishing to loopback is still what `memcp up` does with
no flags, and `test_deploy_plan.py` is what holds that. What is asserted here is the
opt-in path — that it is reachable, that it is checked honestly, and that the
security position it lands in is narrower than the default rather than wider.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings

from memcp.deploy import cli, runner
from memcp.deploy import compose as compose_yaml
from memcp.deploy.smoke import SmokeError, SmokeResult
from memcp.deploy.stacks import BACKENDS, StackOptions, build

EXTERNAL = "https://memory.example.com"


def plan(backend: str = "sqlite", **overrides: object):
    options = StackOptions(
        build_context="..",
        build_dockerfile="Dockerfile",
        directory=".memcp",
        **overrides,  # type: ignore[arg-type]
    )
    return build(backend, options)


def compose_doc(backend: str = "sqlite", **overrides: object) -> dict:
    return yaml.safe_load(compose_yaml.render(plan(backend, **overrides).to_compose(), ""))


def unpublished(backend: str = "sqlite", **overrides: object):
    overrides.setdefault("external_url", EXTERNAL)
    return plan(backend, publish=False, **overrides)


# --- AC1: it can be asked for, and the plan says it was asked for ------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac1_no_service_publishes_a_host_port(backend: str):
    doc = compose_doc(backend, publish=False, external_url=EXTERNAL)
    assert not [name for name, svc in doc["services"].items() if svc.get("ports")]
    assert "ports" not in doc["services"]["memcp"]


def test_ac1_the_plan_says_the_port_was_left_out_deliberately():
    """A reader has to be able to tell this from a plan that forgot to mention one."""
    text = unpublished().render_plan()
    ports = text.split("PUBLISHED PORTS")[1].split("\n\n")[0]
    assert "--no-publish" in ports
    assert "because you asked for it" in ports
    assert "container port 8080" in ports


def test_ac1_the_json_plan_carries_the_choice_as_a_field():
    doc = json.loads(unpublished(network="edge").to_json())
    assert doc["publish_host_port"] is False
    assert doc["ports"] == []
    assert doc["container_port"] == 8080
    assert doc["client_url"] == f"{EXTERNAL}/mcp"
    assert {"name": "edge", "external": True} in doc["networks"]


def test_ac1_a_published_deployment_still_reads_exactly_as_it_did():
    text = plan().render_plan()
    assert "127.0.0.1:8080 -> memcp:8080" in text
    assert "--no-publish" not in text
    assert json.loads(plan().to_json())["publish_host_port"] is True


# --- AC2: the platform's router can still reach it ---------------------------


def test_ac2_memcp_joins_the_named_external_network_without_creating_it():
    doc = compose_doc(publish=False, external_url=EXTERNAL, network="dokploy-network")
    assert doc["networks"]["dokploy-network"] == {"external": True}
    assert doc["services"]["memcp"]["networks"] == ["default", "dokploy-network"]


def test_ac2_naming_a_network_does_not_cut_memcp_off_from_its_engine():
    """A service that declares any network stops joining the default implicitly.

    memcp reaches mem0 by service name over the project network, so the moment it
    names an external one it has to name `default` too or the stack silently loses
    its own backend.
    """
    doc = compose_doc("mem0", publish=False, external_url=EXTERNAL, network="edge")
    assert doc["services"]["memcp"]["networks"] == ["default", "edge"]
    assert doc["networks"]["default"] is None
    # The engine and datastore keep no explicit network, which is how compose puts
    # them on the project's own and nowhere else.
    assert "networks" not in doc["services"]["mem0"]
    assert "networks" not in doc["services"]["postgres"]


def test_ac2_no_network_block_at_all_when_none_is_named():
    """The default stack's compose file does not change shape for this feature."""
    assert "networks" not in compose_doc()
    assert "networks" not in compose_doc(publish=False, external_url=EXTERNAL)


def test_ac2_the_plan_names_the_networks_and_who_owns_them():
    text = unpublished(network="dokploy-network").render_plan()
    assert "NETWORKS" in text
    assert "dokploy-network  existing network, joined not created" in text
    assert "default  created by compose for this deployment" in text


# --- AC3: --wait still gates on health ---------------------------------------


def test_ac3_health_is_checked_inside_the_container_so_it_survives_no_host_port():
    svc = compose_doc(publish=False, external_url=EXTERNAL)["services"]["memcp"]
    assert "http://localhost:8080/health" in " ".join(svc["healthcheck"]["test"])
    assert svc["healthcheck"]["retries"] == 20


def test_ac3_up_still_blocks_on_compose_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    runner.compose_up(tmp_path, 600)
    assert "--wait" in seen[0]
    assert "--wait-timeout" in seen[0]


# --- AC4: nothing reports a success it did not perform -----------------------


def test_ac4_the_first_memory_check_runs_inside_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """`--smoke` has no host route to take, so it takes the one that exists."""
    calls: dict = {}
    monkeypatch.setattr(runner, "require_docker", lambda: None)
    monkeypatch.setattr(runner, "compose_up", lambda directory, timeout: None)
    monkeypatch.setattr(
        cli, "first_memory", lambda *a, **k: pytest.fail("dialled the host with no host port")
    )
    monkeypatch.setattr(
        runner,
        "smoke_inside_container",
        lambda directory, token, port, **kw: (
            calls.update(port=port, token=token)
            or SmokeResult(seconds=1.5, memory_id="m1", matched="")
        ),
    )

    code = cli.main(
        ["up", "--dir", str(tmp_path), "--no-publish", "--external-url", EXTERNAL, "--smoke"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert calls["port"] == 8080
    assert "First memory stored and retrieved over MCP in 1.50s" in out
    assert "Checked from inside the container" in out
    assert f"What this did not check is the route to {EXTERNAL}/mcp" in out


def test_ac4_a_published_deployment_is_still_checked_over_its_published_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    urls: list[str] = []
    monkeypatch.setattr(runner, "require_docker", lambda: None)
    monkeypatch.setattr(runner, "compose_up", lambda directory, timeout: None)
    monkeypatch.setattr(
        runner,
        "smoke_inside_container",
        lambda *a, **k: pytest.fail("skipped the route a client actually takes"),
    )
    monkeypatch.setattr(
        cli,
        "first_memory",
        lambda url, token: urls.append(url) or SmokeResult(seconds=0.4, memory_id="m", matched=""),
    )

    assert cli.main(["up", "--dir", str(tmp_path), "--port", "9091", "--smoke"]) == 0
    assert urls == ["http://127.0.0.1:9091/mcp"]
    assert "Checked over the published port at 127.0.0.1:9091" in capsys.readouterr().out


def test_ac4_verify_reads_the_route_from_what_up_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])
    monkeypatch.setattr(
        cli, "first_memory", lambda *a, **k: pytest.fail("dialled the host with no host port")
    )
    monkeypatch.setattr(
        runner,
        "smoke_inside_container",
        lambda *a, **k: SmokeResult(seconds=0.9, memory_id="m", matched=""),
    )
    assert cli.main(["verify", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "from inside the container — this deployment publishes no host port" in out
    assert f"whether {EXTERNAL}/mcp reaches this container is your platform's to answer" in out


def test_ac4_verify_url_checks_the_route_only_the_platform_can_provide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])
    urls: list[str] = []
    monkeypatch.setattr(
        cli,
        "first_memory",
        lambda url, token: urls.append(url) or SmokeResult(seconds=0.2, memory_id="m", matched=""),
    )
    assert cli.main(["verify", "--dir", str(tmp_path), "--url", f"{EXTERNAL}/mcp"]) == 0
    assert urls == [f"{EXTERNAL}/mcp"]
    assert f"over {EXTERNAL}/mcp" in capsys.readouterr().out


def test_ac4_a_failed_check_says_which_route_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])

    def boom(*a, **k):
        raise SmokeError("the deployment rejected the token")

    monkeypatch.setattr(runner, "smoke_inside_container", boom)
    assert cli.main(["verify", "--dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "FAILED (from inside the container" in err
    assert "rejected the token" in err


def test_ac4_verify_on_an_older_deployment_keeps_todays_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A deployment provisioned before `deployment.json` existed still verifies."""
    _provision(tmp_path, monkeypatch, [])
    (tmp_path / runner.STATE_FILENAME).unlink()
    urls: list[str] = []
    monkeypatch.setattr(
        cli,
        "first_memory",
        lambda url, token: urls.append(url) or SmokeResult(seconds=0.1, memory_id="m", matched=""),
    )
    assert cli.main(["verify", "--dir", str(tmp_path)]) == 0
    assert urls == ["http://127.0.0.1:8080/mcp"]


def test_ac4_the_in_container_check_pipes_the_code_and_the_token_rather_than_arguing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The exec route, asserted without Docker: what is run, and what is not on argv.

    The script goes in on stdin because the container may be running a memcp released
    before this check existed — `--memcp-source pypi` builds the version on PyPI. The
    token goes the same way so it reaches neither the host's process list nor the
    container's environment.
    """
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, input=kwargs.get("input"))
        return subprocess.CompletedProcess(
            cmd, 0, stdout='MEMCP_SMOKE_RESULT {"seconds": 2.0, "id": "abc"}\n', stderr=""
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.smoke_inside_container(tmp_path, "tok-secret", 8080)

    assert result.seconds == 2.0
    assert result.memory_id == "abc"
    assert captured["cmd"][-4:] == ["exec", "-T", "memcp", "python", "-"][1:]
    assert "tok-secret" not in " ".join(captured["cmd"])
    assert "def first_memory(" in captured["input"]
    assert "'http://localhost:8080/mcp'" in captured["input"]
    assert "'tok-secret'" in captured["input"]


def test_ac4_an_exec_that_never_ran_is_not_reported_as_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such service"),
    )
    with pytest.raises(SmokeError, match="could not be run inside the container"):
        runner.smoke_inside_container(tmp_path, "tok", 8080)


# --- AC5: the snippet prints a URL you can paste -----------------------------


def test_ac5_the_client_snippet_prints_the_platform_url_not_localhost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])
    out = capsys.readouterr().out
    assert f'"url": "{EXTERNAL}/mcp"' in out
    assert "localhost" not in out.split("Add this to your MCP client configuration:")[1]
    assert "No host port is published" in out


def test_ac5_no_publish_without_an_address_is_refused_with_both_ways_out(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """Guessing would print a snippet that looks right and connects to nothing."""
    assert cli.main(["plan", "--dir", str(tmp_path), "--no-publish"]) == 1
    err = capsys.readouterr().err
    assert "--external-url https://memory.example.com" in err
    assert "--external-url http://memcp:8080" in err
    assert list(tmp_path.iterdir()) == []


def test_ac5_a_url_with_no_hostname_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture):
    code = cli.main(
        ["plan", "--dir", str(tmp_path), "--no-publish", "--external-url", "memory.example.com"]
    )
    assert code == 1
    assert "not a URL memcp can read a hostname from" in capsys.readouterr().err


def test_ac5_an_external_url_already_ending_in_mcp_is_not_doubled():
    assert unpublished(external_url=f"{EXTERNAL}/mcp").client_url == f"{EXTERNAL}/mcp"
    assert unpublished(external_url=f"{EXTERNAL}/").client_url == f"{EXTERNAL}/mcp"


# --- AC6: Host validation admits the name the deployment answers to ----------


def _env(deployment) -> dict[str, str]:
    return {e.name: e.value for e in deployment.memcp_service.env}


def test_ac6_the_proxy_hostname_is_admitted_without_anyone_remembering_to_add_it():
    """SEC-2026-0063's open half: Host validation is off unless this is set."""
    hosts = _env(unpublished())["MEMCP_ALLOWED_HOSTS"].split(",")
    assert "memory.example.com" in hosts
    # A proxy on a non-default port sends `name:port`; one on 80 or 443 sends neither.
    assert "memory.example.com:*" in hosts


def test_ac6_an_explicit_port_in_the_url_is_admitted_as_given():
    hosts = _env(unpublished(external_url="http://memcp:8080")).get("MEMCP_ALLOWED_HOSTS", "")
    assert "memcp:8080" in hosts.split(",")


def test_ac6_loopback_stays_admitted_so_the_in_container_check_works():
    hosts = _env(unpublished())["MEMCP_ALLOWED_HOSTS"].split(",")
    assert "localhost:*" in hosts
    assert "127.0.0.1:*" in hosts


def test_ac6_a_published_deployment_behind_a_proxy_gets_the_same_treatment():
    hosts = _env(plan(external_url=EXTERNAL))["MEMCP_ALLOWED_HOSTS"].split(",")
    assert "memory.example.com" in hosts
    assert "127.0.0.1:*" in hosts


def _matches(allowed_hosts: list[str], host_header: str) -> bool:
    """Drive the SDK's own matcher rather than compare allow-list strings."""
    settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=[]
    )
    return TransportSecurityMiddleware(settings)._validate_host(host_header)


def test_ac6_an_ipv6_external_url_is_bracketed_so_the_sdk_admits_it():
    """JAR-591: `urlsplit(...).hostname` strips the brackets a client's Host keeps."""
    hosts = _env(unpublished(external_url="http://[2001:db8::1]:8080"))[
        "MEMCP_ALLOWED_HOSTS"
    ].split(",")
    assert _matches(hosts, "[2001:db8::1]:8080")
    assert not _matches(hosts, "2001:db8::1:8080")


def test_ac6_a_mixed_case_external_url_admits_both_spellings_and_nothing_else():
    """JAR-591: `urlsplit` lowercases; a proxy that forwards Host verbatim does not."""
    hosts = _env(unpublished(external_url="https://MEMORY.Example.COM"))[
        "MEMCP_ALLOWED_HOSTS"
    ].split(",")
    assert _matches(hosts, "MEMORY.Example.COM")
    assert _matches(hosts, "memory.example.com")
    assert not _matches(hosts, "evil.example.com")


# --- AC7: the security position does not move --------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac7_the_token_is_still_minted_and_never_defaulted(backend: str):
    deployment = unpublished(backend)
    assert _env(deployment)["MEMCP_AUTH_TOKENS"] == "${MEMCP_TOKEN}:owner"
    minted = [s.name for s in deployment.minted_secrets]
    assert "MEMCP_TOKEN" in minted


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac7_g2_strengthens_rather_than_relaxes(backend: str):
    """G2 says only memcp publishes, and to loopback. Publishing nothing is narrower.

    The property G2 protects is that the engine and its datastore are behind memcp's
    bearer gate rather than beside it. With no published port that holds by a wider
    margin: there is no host route to anything in the stack at all.
    """
    doc = compose_doc(backend, publish=False, external_url=EXTERNAL)
    assert all("ports" not in svc for svc in doc["services"].values())
    assert "docker.sock" not in compose_yaml.render(unpublished(backend).to_compose(), "")


def test_ac7_the_deployment_state_file_carries_no_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])
    token = runner.read_token(tmp_path)
    assert token
    state_text = (tmp_path / runner.STATE_FILENAME).read_text()
    assert token not in state_text
    assert json.loads(state_text)["publish_host_port"] is False


def test_ac7_the_state_file_is_declared_before_it_is_written():
    """C6: nothing is created that the plan did not declare."""
    assert any("deployment.json" in f for f in plan().generated_files)


# --- AC8: re-provisioning does not destroy memories --------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_ac8_volumes_and_secrets_do_not_depend_on_the_publishing_choice(backend: str):
    published, hidden = plan(backend), unpublished(backend)
    assert [v.name for v in published.volumes] == [v.name for v in hidden.volumes]
    assert [(s.name, s.minted) for s in published.secrets] == [
        (s.name, s.minted) for s in hidden.secrets
    ]
    assert published.project_name == hidden.project_name


def test_ac8_turning_publishing_off_on_an_existing_deployment_keeps_its_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """C5 still holds across the flag: same project, same volumes, same credential."""
    _provision(tmp_path, monkeypatch, [])
    before = runner.read_token(tmp_path)
    assert json.loads((tmp_path / runner.STATE_FILENAME).read_text())["host_port"] == 8080

    _provision(tmp_path, monkeypatch, ["--no-publish", "--external-url", EXTERNAL])
    after = runner.read_token(tmp_path)
    state = json.loads((tmp_path / runner.STATE_FILENAME).read_text())

    assert after == before
    assert state["publish_host_port"] is False
    assert state["host_port"] is None
    compose_text = (tmp_path / runner.COMPOSE_FILENAME).read_text()
    assert "memcp_data:/data" in compose_text
    assert "ports:" not in compose_text


# --- helpers -----------------------------------------------------------------


def _provision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str]) -> None:
    """`memcp up` with the container runtime stubbed out — files, not containers."""
    monkeypatch.setattr(runner, "require_docker", lambda: None)
    monkeypatch.setattr(runner, "compose_up", lambda directory, timeout: None)
    assert cli.main(["up", "--dir", str(tmp_path), *flags]) == 0
