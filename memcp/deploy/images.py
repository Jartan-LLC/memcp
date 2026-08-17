"""Every image a deployment pulls, pinned by digest.

A tag is a moving target: `pgvector/pgvector:pg17` today and `pgvector/pgvector:pg17`
next month are different bytes, and a deployment that re-pulls silently changes what
it runs. Digests are what make `memcp up` reproducible and what keep an upstream
account compromise from reaching an installed deployment (the class recorded as
SEC-2026-0051 and SEC-2026-0052).

Moving a pin is a commit here, deliberately. The tag beside each digest is what it
resolved from, so the next person can re-resolve it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinnedImage:
    """An image reference and the tag its digest was resolved from."""

    repository: str
    tag: str
    digest: str

    @property
    def reference(self) -> str:
        return f"{self.repository}@{self.digest}"

    def __str__(self) -> str:
        return self.reference


# Resolved 2026-08-17 from the Docker Hub registry API.
PYTHON_BASE = PinnedImage(
    repository="python",
    tag="3.12-slim",
    digest="sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a",
)

PGVECTOR = PinnedImage(
    repository="pgvector/pgvector",
    tag="pg17",
    digest="sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f",
)

# mem0 publishes no image this repository is willing to depend on: the one on Docker
# Hub last moved in September 2025 and is not the revision slice A's conformance run
# proved the adapter against. So the mem0 service is built from the fork at the SHA
# below — the same pin ci/mem0 uses, asserted equal by tests/test_deploy_plan.py.
MEM0_SOURCE_REPO = "https://github.com/Jartan-LLC/mem0.git"
MEM0_SOURCE_PIN = "42fe3511615cb8aa8c12363b1c8733da9d51ac24"

ALL_IMAGES: tuple[PinnedImage, ...] = (PYTHON_BASE, PGVECTOR)
