"""The deployment's .env — minted once, 0600, never regenerated behind your back.

Three rules the security gate turns on, all enforced here rather than by convention:

- **Nothing is defaulted.** Every credential is `secrets.token_urlsafe(32)` at first
  `up`, including the memcp↔engine link on a private network. No literal password
  ships in this repository (G3).
- **Nothing rotates silently.** A second `up` reads the values already on disk. A
  token changes only when `memcp rotate-token` is run, which is a deliberate act with
  an obvious output (G5, C5).
- **A secret memcp cannot mint stops the run.** A provider API key is named by its
  exact variable and `up` refuses, rather than starting a stack that half-works (C3).
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from memcp.deploy.model import Deployment, RequiredSecret

ENV_FILENAME = ".env"
GITIGNORE_FILENAME = ".gitignore"

# Only ever matches a plain KEY=VALUE line. Deployment .env files are written by this
# module, so no quoting or continuation syntax has to be understood.
_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

TOKEN_BYTES = 32


class MissingSecretError(RuntimeError):
    """An operator-supplied secret is absent. Carries the variable names."""

    def __init__(self, missing: list[RequiredSecret]):
        self.missing = missing
        names = ", ".join(s.name for s in missing)
        lines = [f"Missing required configuration: {names}", ""]
        for secret in missing:
            lines.append(f"  {secret.name}")
            lines.append(f"    {secret.description}")
            if secret.how_to_obtain:
                lines.append(f"    {secret.how_to_obtain}")
        lines.append("")
        lines.append(
            "memcp did not create anything. Set the variable(s) in your shell or add "
            "them to the deployment's .env, then run `memcp up` again."
        )
        super().__init__("\n".join(lines))


@dataclass
class EnvFile:
    """The deployment's .env, read and written at 0600."""

    path: Path

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        values: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _LINE.match(line)
            if match:
                values[match.group(1)] = match.group(2)
        return values

    def write(self, values: dict[str, str]) -> None:
        body = [
            "# memcp deployment configuration — generated, holds live credentials.",
            "# Mode 0600 and gitignored on purpose. Do not commit it, do not paste it.",
            "# Replace the memcp token with `memcp rotate-token`.",
            "",
        ]
        body += [f"{k}={v}" for k, v in sorted(values.items())]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create at 0600 before any byte is written, so the secret is never briefly
        # world-readable on a shared machine.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
        os.chmod(self.path, 0o600)

    @property
    def mode(self) -> int:
        return stat.S_IMODE(self.path.stat().st_mode)


def mint() -> str:
    """A fresh credential. The only source of one in this package."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def write_gitignore(directory: Path) -> Path:
    """Make the deployment directory uncommittable by a normal workflow (G4)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / GITIGNORE_FILENAME
    path.write_text(
        "# The deployment directory holds live credentials and generated stack files.\n"
        "# memcp writes this so a normal `git add .` cannot pick them up.\n"
        "*\n",
        encoding="utf-8",
    )
    return path


def resolve_secrets(
    deployment: Deployment,
    directory: Path,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Return (env values, names newly minted this call).

    Precedence for an operator-supplied secret: the process environment first, then
    the existing .env. Minted secrets come from the .env and are only generated when
    absent — a second `up` never rotates one (G5).
    """
    env: dict[str, str] = dict(os.environ) if environ is None else environ
    existing = EnvFile(directory / ENV_FILENAME).read()
    values = dict(existing)
    minted: list[str] = []

    missing: list[RequiredSecret] = []
    for secret in deployment.secrets:
        supplied = env.get(secret.name) or existing.get(secret.name)
        if supplied:
            values[secret.name] = supplied
            continue
        if secret.minted:
            values[secret.name] = mint()
            minted.append(secret.name)
            continue
        missing.append(secret)

    if missing:
        raise MissingSecretError(missing)

    return values, minted
