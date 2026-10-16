"""The keys file: what startup refuses, and how a token resolves to its key.

Every refusal names a key by its position, never by a secret: these messages reach the
startup log, and a malformed secret may be a token pasted where its hash belongs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memcp.http.auth import load_keys
from memcp.identity import Grant, Key
from tests.keys import secret, write_keys_file

TOKEN = "laptop-token-not-a-real-credential"
OTHER = "ci-token-not-a-real-credential"
GRANT = 'grants = [{ space = "team", relation = "writer" }]'


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "keys.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _key(name: str = "laptop", secrets: str | None = None, grants: str = GRANT) -> str:
    secrets = secrets or f'["{secret(TOKEN)}"]'
    return f'[[keys]]\nname = "{name}"\nsecrets = {secrets}\n{grants}\n'


def _refused(path: Path, match: str) -> str:
    with pytest.raises(ValueError, match=match) as excinfo:
        load_keys(str(path))
    message = str(excinfo.value)
    assert TOKEN not in message
    assert secret(TOKEN) not in message
    assert secret(TOKEN).removeprefix("sha256:") not in message
    return message


# --- the file itself ---------------------------------------------------------


def test_a_missing_file_is_refused(tmp_path: Path):
    _refused(tmp_path / "absent.toml", "MEMCP_KEYS_FILE .* can't be read")


def test_a_directory_is_refused(tmp_path: Path):
    _refused(tmp_path, "MEMCP_KEYS_FILE .* can't be read")


def test_a_file_that_is_not_toml_is_refused_without_quoting_it(tmp_path: Path):
    path = _write(tmp_path, f'[[keys]]\nname = "laptop"\nsecrets = [{TOKEN}]\n')
    _refused(path, "isn't valid TOML")


def test_a_file_that_is_not_utf8_is_refused_by_name(tmp_path: Path):
    path = tmp_path / "keys.toml"
    path.write_bytes(_key().encode("utf-16"))
    message = _refused(path, "MEMCP_KEYS_FILE .*keys.toml isn't UTF-8")
    assert "0x" not in message


@pytest.mark.parametrize("text", ["", "keys = []\n"], ids=["empty file", "no keys"])
def test_a_file_with_no_keys_is_refused(tmp_path: Path, text: str):
    _refused(_write(tmp_path, text), "keys")


# --- fields --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"version = 1\n{_key()}",
        f'{_key()}token = "{TOKEN}"\n',
        _key(grants=f'grants = [{{ space = "team", relation = "writer", token = "{TOKEN}" }}]'),
    ],
    ids=["top level", "in a key", "in a grant"],
)
def test_an_unknown_field_is_refused(tmp_path: Path, text: str):
    _refused(_write(tmp_path, text), "Extra inputs are not permitted")


@pytest.mark.parametrize("field", ["name", "secrets", "grants"])
def test_a_missing_field_is_refused(tmp_path: Path, field: str):
    lines = [line for line in _key().splitlines() if not line.startswith(field)]
    message = _refused(_write(tmp_path, "\n".join(lines) + "\n"), "key 1")
    assert field in message


# --- names ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "has space", "a" * 65, "slash/name", "é"])
def test_a_name_outside_the_allowed_set_is_refused(tmp_path: Path, name: str):
    _refused(_write(tmp_path, _key(name=name)), "key 1.*name")


def test_a_name_of_64_allowed_characters_is_accepted(tmp_path: Path):
    name = "a-Z_0.9" + "x" * 57
    keys = load_keys(str(_write(tmp_path, _key(name=name))))
    assert keys.keys() == [Key(name=name, grant=Grant(space="team", relation="writer"))]


def test_a_duplicate_name_is_refused(tmp_path: Path):
    path = write_keys_file(
        tmp_path / "keys.toml",
        ("laptop", TOKEN, "team", "writer"),
        ("laptop", OTHER, "team", "reader"),
    )
    message = _refused(path, "keys 1 and 2")
    assert "laptop" in message


# --- secrets -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        TOKEN,
        secret(TOKEN).upper().replace("SHA256", "sha256"),
        secret(TOKEN)[:-1],
        secret(TOKEN) + "0",
        secret(TOKEN).removeprefix("sha256:"),
        "sha512:" + secret(TOKEN).removeprefix("sha256:"),
    ],
    ids=["a plaintext token", "upper-case hex", "too short", "too long", "no prefix", "sha512"],
)
def test_a_malformed_secret_is_refused_without_quoting_it(tmp_path: Path, value: str):
    path = _write(tmp_path, _key(secrets=f'["{value}"]'))
    message = _refused(path, "key 1.*secrets")
    assert value not in message


def test_a_key_with_no_secret_is_refused(tmp_path: Path):
    _refused(_write(tmp_path, _key(secrets="[]")), "key 1.*secrets")


def test_a_secret_under_two_keys_is_refused(tmp_path: Path):
    path = write_keys_file(
        tmp_path / "keys.toml",
        ("laptop", OTHER, "team", "writer"),
        ("ci", TOKEN, "team", "reader"),
        ("phone", TOKEN, "team", "writer"),
    )
    _refused(path, "keys 2 and 3 .*same secret")


def test_the_same_secret_twice_under_one_key_is_refused(tmp_path: Path):
    path = _write(tmp_path, _key(secrets=f'["{secret(TOKEN)}", "{secret(TOKEN)}"]'))
    _refused(path, "key 1 lists the same secret twice")


# --- grants --------------------------------------------------------------------


@pytest.mark.parametrize(
    "grants",
    [
        "grants = []",
        'grants = [{ space = "team", relation = "writer" }, { space = "b", relation = "reader" }]',
    ],
    ids=["none", "two"],
)
def test_a_key_needs_exactly_one_grant(tmp_path: Path, grants: str):
    _refused(_write(tmp_path, _key(grants=grants)), "key 1.*grants")


@pytest.mark.parametrize("relation", ["admin", "Writer", "manager", ""])
def test_an_unknown_relation_is_refused(tmp_path: Path, relation: str):
    grants = f'grants = [{{ space = "team", relation = "{relation}" }}]'
    _refused(_write(tmp_path, _key(grants=grants)), "key 1.*relation")


def test_a_space_that_is_not_a_string_is_refused(tmp_path: Path):
    grants = 'grants = [{ space = 7, relation = "writer" }]'
    _refused(_write(tmp_path, _key(grants=grants)), "key 1.*space")


# --- resolution ----------------------------------------------------------------


def test_each_token_resolves_to_its_own_key(tmp_path: Path):
    keys = load_keys(
        str(
            write_keys_file(
                tmp_path / "keys.toml",
                ("laptop", TOKEN, "team", "writer"),
                ("ci", OTHER, "team", "reader"),
            )
        )
    )
    assert keys.resolve(TOKEN) == Key(name="laptop", grant=Grant(space="team", relation="writer"))
    assert keys.resolve(OTHER) == Key(name="ci", grant=Grant(space="team", relation="reader"))


def test_both_secrets_of_a_rotating_key_resolve_to_it(tmp_path: Path):
    path = _write(tmp_path, _key(secrets=f'["{secret(TOKEN)}", "{secret(OTHER)}"]'))
    keys = load_keys(str(path))
    assert keys.resolve(TOKEN) == keys.resolve(OTHER) == keys.keys()[0]
    assert len(keys.keys()) == 1


@pytest.mark.parametrize(
    "token",
    ["", "wrong-token", TOKEN[:-1], TOKEN + "\x00", TOKEN.upper(), secret(TOKEN)],
    ids=["empty", "unknown", "a prefix", "a trailing nul", "other case", "the hash itself"],
)
def test_a_token_no_key_lists_resolves_to_nothing(tmp_path: Path, token: str):
    keys = load_keys(str(_write(tmp_path, _key())))
    assert keys.resolve(token) is None


def test_keys_are_listed_once_each_in_file_order(tmp_path: Path):
    path = _write(
        tmp_path,
        _key(name="b", secrets=f'["{secret(TOKEN)}", "{secret(OTHER)}"]')
        + "\n"
        + _key(name="a", secrets=f'["{secret("third")}"]'),
    )
    listed = load_keys(str(path)).keys()
    assert [k.name for k in listed] == ["b", "a"]
