"""What `memcp up` would create, asserted before it creates it.

These are the security gate's structural items (JAR-412 `b73d1c77`) turned into
tests, so a future change that reopens one fails here rather than at review:

- G1 no path from memcp to the Docker daemon
- G2 only memcp publishes a port, and it binds loopback
- G3 every credential is an interpolation, never a literal
- G4 no secret value in plan output
- G6 every image pinned by digest
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from memcp.deploy import compose as compose_yaml
from memcp.deploy.images import ALL_IMAGES, COGNEE, MEM0_SOURCE_PIN
from memcp.deploy.model import SECRET_PLACEHOLDER
from memcp.deploy.stacks import BACKENDS, StackOptions, build

REPO_ROOT = Path(__file__).resolve().parents[1]


def plan(backend: str, **overrides: object) -> object:
    options = StackOptions(
        build_context=str(overrides.pop("build_context", "..")),
        build_dockerfile="Dockerfile",
        directory=".memcp",
        **overrides,  # type: ignore[arg-type]
    )
    return build(backend, options)


def compose_doc(backend: str, **overrides: object) -> dict:
    rendered = compose_yaml.render(plan(backend).to_compose(), "")  # type: ignore[attr-defined]
    return yaml.safe_load(rendered)


@pytest.mark.parametrize("backend", BACKENDS)
def test_compose_is_parseable_yaml(backend: str):
    """The hand-rolled emitter produces YAML, not something that only looks like it."""
    doc = compose_doc(backend)
    assert doc["name"] == "memcp"
    assert "memcp" in doc["services"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_g1_nothing_mounts_the_docker_socket(backend: str):
    """G1 — a provisioned memcp must hold no route to the Docker daemon."""
    rendered = compose_yaml.render(plan(backend).to_compose(), "")  # type: ignore[attr-defined]
    assert "docker.sock" not in rendered
    assert "/var/run/docker" not in rendered


@pytest.mark.parametrize("backend", BACKENDS)
def test_g2_only_memcp_publishes_a_port_and_it_is_loopback(backend: str):
    """G2 — the engine and its datastore sit behind memcp, not beside it."""
    doc = compose_doc(backend)
    publishing = {name: svc["ports"] for name, svc in doc["services"].items() if svc.get("ports")}
    assert list(publishing) == ["memcp"], f"{list(publishing)} publish a host port"
    assert publishing["memcp"] == ["127.0.0.1:8080:8080"]


def test_g2_engine_and_datastore_have_no_host_port():
    doc = compose_doc("mem0")
    assert "ports" not in doc["services"]["mem0"]
    assert "ports" not in doc["services"]["postgres"]


def test_binding_all_interfaces_is_explicit():
    """Not forbidden, but never the default — the operator has to type it."""
    doc = yaml.safe_load(
        compose_yaml.render(plan("sqlite", bind="0.0.0.0").to_compose(), "")  # type: ignore[attr-defined]
    )
    assert doc["services"]["memcp"]["ports"] == ["0.0.0.0:8080:8080"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_g3_every_secret_is_an_interpolation(backend: str):
    """G3 — no credential literal ships in a generated file, adopted or otherwise."""
    deployment = plan(backend)
    for service in deployment.services:  # type: ignore[attr-defined]
        for env in service.env:
            if env.secret:
                assert env.value.startswith("${"), f"{env.name} carries a literal"


def test_g3_no_upstream_password_is_adopted():
    """mem0's own compose ships fixed values; none of them may appear here."""
    rendered = compose_yaml.render(plan("mem0").to_compose(), "")  # type: ignore[attr-defined]
    for leaked in ("conformance", "postgres:postgres", "AUTH_DISABLED=true", "mem0ai"):
        assert leaked not in rendered


def test_g3_mem0_link_is_authenticated_on_a_private_network():
    deployment = plan("mem0")
    mem0 = next(s for s in deployment.services if s.name == "mem0")  # type: ignore[attr-defined]
    env = {e.name: e for e in mem0.env}
    assert env["AUTH_DISABLED"].value == "false"
    assert env["ADMIN_API_KEY"].secret


@pytest.mark.parametrize("backend", BACKENDS)
def test_g4_plan_output_never_shows_a_secret(backend: str):
    """G4 — a plan is something you can paste into an issue."""
    deployment = plan(backend)
    text = deployment.render_plan() + deployment.to_json()  # type: ignore[attr-defined]
    for service in deployment.services:  # type: ignore[attr-defined]
        for env in service.env:
            if env.secret:
                assert env.value not in text
                assert SECRET_PLACEHOLDER in text


@pytest.mark.parametrize("backend", BACKENDS)
def test_g6_every_image_is_digest_pinned(backend: str):
    """G6 — a tag is a moving target; a digest is not."""
    doc = compose_doc(backend)
    for name, svc in doc["services"].items():
        if "image" in svc:
            assert "@sha256:" in svc["image"], f"{name} runs an unpinned tag"


def test_g6_pins_are_well_formed():
    for image in ALL_IMAGES:
        assert image.digest.startswith("sha256:")
        assert len(image.digest) == len("sha256:") + 64


def test_mem0_pin_matches_the_conformance_stack():
    """One pin for the mem0 fork, in two places that must not drift.

    ci/mem0 proved the adapter against this revision; provisioning must stand up the
    same one, or `memcp up` ships something CI never exercised.
    """
    ci_pin = (REPO_ROOT / "ci" / "mem0" / "mem0.pin").read_text().strip()
    assert ci_pin == MEM0_SOURCE_PIN


def test_c6_plan_declares_containers_volumes_ports_and_env():
    """C6 — everything provisioned is declared before it is created."""
    text = plan("mem0").render_plan()  # type: ignore[attr-defined]
    for heading in ("CONTAINERS", "PUBLISHED PORTS", "VOLUMES", "ENVIRONMENT", "SECRETS"):
        assert heading in text
    for name in ("postgres", "mem0", "memcp"):
        assert name in text
    for volume in ("postgres_data", "mem0_history"):
        assert volume in text


@pytest.mark.parametrize(
    ("backend", "variable"),
    [("mem0", "OPENAI_API_KEY"), ("cognee", "COGNEE_LLM_API_KEY")],
)
def test_c3_a_backend_that_needs_a_key_names_the_variable(backend: str, variable: str):
    """C3 — no secret is invented silently."""
    required = plan(backend).operator_secrets  # type: ignore[attr-defined]
    assert [s.name for s in required] == [variable]
    assert variable in required[0].how_to_obtain


def test_keyless_stacks_require_nothing_from_the_operator():
    """C2's clause: sqlite reaches a durable brain with no signup and no key."""
    for backend in ("sqlite", "in_memory"):
        assert plan(backend).operator_secrets == []  # type: ignore[attr-defined]
    assert plan("sqlite").durable is True  # type: ignore[attr-defined]
    assert plan("in_memory").durable is False  # type: ignore[attr-defined]


def test_only_durable_stacks_declare_a_volume():
    assert plan("sqlite").volumes  # type: ignore[attr-defined]
    assert plan("mem0").volumes  # type: ignore[attr-defined]
    assert plan("cognee").volumes  # type: ignore[attr-defined]
    assert plan("in_memory").volumes == []  # type: ignore[attr-defined]


def test_cognee_pin_matches_the_conformance_stack():
    """One digest for the cognee image, in two places that must not drift.

    ci/cognee proved the adapter against this release; provisioning must stand up the
    same one, or `memcp up --backend cognee` ships something CI never exercised.
    """
    compose = (REPO_ROOT / "ci" / "cognee" / "docker-compose.yml").read_text()
    assert COGNEE.reference in compose


def test_cognee_turns_on_the_isolation_it_depends_on():
    """memcp's tenant boundary on this backend *is* cognee's per-user access control.

    Provisioned with it off, every memcp tenant would resolve to cognee's one default
    user and fifteen agents would read each other's memories.
    """
    deployment = plan("cognee")
    engine = next(s for s in deployment.services if s.name == "cognee")  # type: ignore[attr-defined]
    env = {e.name: e.value for e in engine.env}
    assert env["ENABLE_BACKEND_ACCESS_CONTROL"] == "true"
    assert env["REQUIRE_AUTHENTICATION"] == "true"


def test_cognee_tenant_secret_is_minted_and_never_shown():
    deployment = plan("cognee")
    minted = {s.name for s in deployment.minted_secrets}  # type: ignore[attr-defined]
    assert "COGNEE_TENANT_SECRET" in minted
    env = {e.name: e for e in deployment.memcp_service.env}  # type: ignore[attr-defined]
    assert env["COGNEE_TENANT_SECRET"].secret


def test_cognee_plan_names_every_egress_destination():
    """C6 — a plan that omits where memories are sent is the criterion failing.

    Cognee makes two outbound calls per memory, to an extractor and an embedder, and
    `--llm-base-url` redirects both. Both have to appear.
    """
    text = plan("cognee", llm_base_url="http://localhost:11434/v1").render_plan()  # type: ignore[attr-defined]
    assert "LLM_ENDPOINT=http://localhost:11434/v1" in text
    assert "EMBEDDING_ENDPOINT=http://localhost:11434/v1" in text


def test_unknown_backend_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="sqlite"):
        plan("neo4j")


def test_dns_rebinding_protection_is_set_explicitly():
    """The SDK leaves it off for a non-loopback bind, and a container binds 0.0.0.0."""
    deployment = plan("sqlite")
    env = {e.name: e.value for e in deployment.memcp_service.env}  # type: ignore[attr-defined]
    assert env["MEMCP_HOST"] == "0.0.0.0"
    assert "127.0.0.1:*" in env["MEMCP_ALLOWED_HOSTS"]
