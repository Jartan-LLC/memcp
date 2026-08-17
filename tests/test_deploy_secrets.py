"""Credential handling: minted once, 0600, never rotated behind your back.

C4 and C5 in the spec, G3 to G5 in the security gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memcp.deploy.runner import materialize, plan_for, prepare, rotate_token
from memcp.deploy.secretstore import (
    ENV_FILENAME,
    EnvFile,
    MissingSecretError,
    mint,
    resolve_secrets,
)
from memcp.deploy.stacks import MEMCP_TOKEN_VAR

KEYLESS_ENV: dict[str, str] = {}


def _prepare(directory: Path, backend: str = "sqlite", environ=KEYLESS_ENV):
    deployment, _ = plan_for(
        backend, directory, port=8080, bind="127.0.0.1", project="memcp", source_spec="pypi"
    )
    return deployment, resolve_secrets(deployment, directory, environ=dict(environ))


def test_c4_a_token_is_minted_not_defaulted(tmp_path: Path):
    _, (values, minted) = _prepare(tmp_path)
    assert minted == [MEMCP_TOKEN_VAR]
    # token_urlsafe(32) is 43 characters of base64url. A short value here would mean
    # something other than the minting path produced it.
    assert len(values[MEMCP_TOKEN_VAR]) >= 40


def test_minted_tokens_are_unique_per_deployment():
    assert mint() != mint()


def test_c5_a_second_up_reuses_the_token_rather_than_rotating_it(tmp_path: Path):
    _, (values, _) = _prepare(tmp_path)
    EnvFile(tmp_path / ENV_FILENAME).write(values)

    _, (again, minted_again) = _prepare(tmp_path)
    assert minted_again == []
    assert again[MEMCP_TOKEN_VAR] == values[MEMCP_TOKEN_VAR]


def test_g3_env_file_is_created_0600(tmp_path: Path):
    env = EnvFile(tmp_path / ENV_FILENAME)
    env.write({"MEMCP_TOKEN": "s3cret"})
    assert env.mode == 0o600


def test_g4_the_deployment_directory_is_uncommittable(tmp_path: Path):
    deployment, source, values, _ = prepare(
        "sqlite",
        tmp_path,
        port=8080,
        bind="127.0.0.1",
        project="memcp",
        source_spec="pypi",
    )
    materialize(deployment, tmp_path, source, values)
    assert (tmp_path / ".gitignore").read_text().strip().endswith("*")


def test_g4_no_secret_reaches_the_compose_file(tmp_path: Path):
    deployment, source, values, _ = prepare(
        "sqlite", tmp_path, port=8080, bind="127.0.0.1", project="memcp", source_spec="pypi"
    )
    materialize(deployment, tmp_path, source, values)
    compose = (tmp_path / "docker-compose.yml").read_text()
    assert values[MEMCP_TOKEN_VAR] not in compose
    assert "${MEMCP_TOKEN}" in compose


def test_g5_rotation_replaces_only_the_token(tmp_path: Path):
    _, (values, _) = _prepare(tmp_path, "mem0", {"OPENAI_API_KEY": "sk-test"})
    EnvFile(tmp_path / ENV_FILENAME).write(values)

    new_token = rotate_token(tmp_path)
    after = EnvFile(tmp_path / ENV_FILENAME).read()

    assert new_token != values[MEMCP_TOKEN_VAR]
    assert after[MEMCP_TOKEN_VAR] == new_token
    for name in ("POSTGRES_PASSWORD", "MEM0_ADMIN_API_KEY", "OPENAI_API_KEY"):
        assert after[name] == values[name], f"{name} changed during a token rotation"
    assert EnvFile(tmp_path / ENV_FILENAME).mode == 0o600


def test_c3_a_missing_provider_key_stops_the_run_and_names_the_variable(tmp_path: Path):
    with pytest.raises(MissingSecretError) as excinfo:
        _prepare(tmp_path, "mem0")
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "--backend sqlite" in message
    assert not (tmp_path / "docker-compose.yml").exists(), "nothing may be created"


def test_a_supplied_key_is_taken_from_the_environment(tmp_path: Path):
    _, (values, _) = _prepare(tmp_path, "mem0", {"OPENAI_API_KEY": "sk-supplied"})
    assert values["OPENAI_API_KEY"] == "sk-supplied"


def test_env_file_round_trips_values_with_awkward_characters(tmp_path: Path):
    env = EnvFile(tmp_path / ENV_FILENAME)
    written = {"A": "x=y=z", "B": "sk-Abc_123-def", "C": ""}
    env.write(written)
    assert env.read() == written
