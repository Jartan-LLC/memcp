"""Inputs to publish-docker.yml: the stable-tag rule and the compose line `attach` rewrites."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TAKES_LATEST = REPO_ROOT / ".github" / "scripts" / "takes-latest.sh"


def _takes_latest(repo: Path, tag: str) -> str:
    out = subprocess.run([TAKES_LATEST, tag], cwd=repo, capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def tagged_repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    for tag in ("v0.9.0", "v0.10.0", "v0.10.1rc1", "v0.11.0-rc.1", "v0.9.1"):
        git("tag", tag)
    return tmp_path


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.10.0", "true"),  # highest stable, ordered as versions, not strings
        ("v0.9.1", "false"),  # a patch on an older line
        ("v0.10.1rc1", "false"),  # PEP 440 pre-release
        ("v0.11.0-rc.1", "false"),  # semver pre-release
    ],
)
def test_only_the_highest_stable_tag_takes_latest(tagged_repo: Path, tag: str, expected: str):
    assert _takes_latest(tagged_repo, tag) == expected


def test_no_stable_tag_answers_false(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert _takes_latest(tmp_path, "v0.1.0rc1") == "false"


def test_compose_image_line_matches_the_release_rewrite():
    """publish-docker.yml's attach job rewrites this exact text to the release's version."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("${MEMCP_VERSION:-latest}") == 1
