"""What a cross-backend round trip loses, declared per backend pair.

This module is the single source of truth. `docs/portability.md` is generated from
it (`python -m memcp.conformance.portability --write`) and a test asserts the file
matches, so the document cannot drift from what the round trip enforces.

The round trip asserts observed losses are a subset of the declared set for the
pair. An undeclared loss fails; it does not pass quietly. A declared loss that no
longer happens is reported as stale rather than failed — the pair may be exercised
against a backend build where the loss is real.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

DOC_PATH = Path("docs/portability.md")
GENERATOR = "python -m memcp.conformance.portability --write"

# Every aspect the round trip compares. An aspect not in this tuple is not checked,
# so adding a field to Memory means adding it here too.
ASPECTS: tuple[str, ...] = (
    "content",
    "scope",
    "metadata",
    "memory_id",
    "created_at",
    "updated_at",
    "history",
)


# Aspects that may never be declared lost. They are the portability claim itself:
# if content or scope can be documented away, the round trip stops proving anything.
INVIOLABLE: tuple[str, ...] = ("content", "scope")


@dataclass(frozen=True)
class Loss:
    aspect: str
    reason: str

    def __post_init__(self) -> None:
        if self.aspect not in ASPECTS:
            raise ValueError(f"Unknown portability aspect {self.aspect!r}. Known: {ASPECTS}")
        if self.aspect in INVIOLABLE:
            raise ValueError(
                f"{self.aspect!r} cannot be declared as an accepted loss. A backend pair "
                "that loses content or scope is not portable; fix the adapter or drop "
                "the pair."
            )


# Losses that follow from how migration works rather than from any one backend:
# import calls add(), which mints a new id and stamps a new created_at.
_IDENTITY_LOSSES: tuple[Loss, ...] = (
    Loss(
        "memory_id",
        "Import calls add() on the target, which mints its own id. Nothing carries "
        "a caller-supplied id, so ids never survive a migration — hold references "
        "by content and scope, not by id.",
    ),
    Loss(
        "created_at",
        "The target stamps its own creation time on import. The original timestamp "
        "is in the export payload but no backend accepts it on write.",
    ),
    Loss(
        "updated_at",
        "An imported memory is newly created on the target, so its edit time resets "
        "to unset regardless of the source's.",
    ),
    Loss(
        "history",
        "export_memories carries current content only. The per-memory change log "
        "that memory_history returns is not exported and cannot be replayed.",
    ),
)

DECLARED_LOSSES: dict[tuple[str, str], tuple[Loss, ...]] = {
    ("in_memory", "in_memory"): _IDENTITY_LOSSES,
    ("in_memory", "mem0"): _IDENTITY_LOSSES,
    ("mem0", "in_memory"): _IDENTITY_LOSSES,
    ("mem0", "mem0"): _IDENTITY_LOSSES,
}

# Notes that are true of a pair but are not a per-memory field loss. Published in
# the document; not asserted by the round trip.
PAIR_NOTES: dict[tuple[str, str], tuple[str, ...]] = {
    ("in_memory", "mem0"): (
        "mem0 stores a content hash and may deduplicate server-side. Import writes "
        "with infer=false, so nothing is re-extracted, but a target that already "
        "holds the same content in the same scope can collapse the write.",
    ),
    ("mem0", "in_memory"): (
        "in_memory search is word-overlap, not vector similarity. Content and scope "
        "survive, but ranking does not — a query's result order after migration is "
        "not the source's order.",
    ),
}


class UndocumentedPairError(KeyError):
    """Raised when a round trip runs for a pair the document does not cover."""


def pair_losses(source: str, target: str) -> tuple[Loss, ...]:
    try:
        return DECLARED_LOSSES[(source, target)]
    except KeyError:
        raise UndocumentedPairError(
            f"No portability record for {source} -> {target}. Add the pair to "
            f"memcp/conformance/portability.py and regenerate {DOC_PATH} with "
            f"`{GENERATOR}`."
        ) from None


def declared_aspects(source: str, target: str) -> frozenset[str]:
    return frozenset(loss.aspect for loss in pair_losses(source, target))


def undeclared(source: str, target: str, observed: set[str]) -> frozenset[str]:
    """Observed losses the document does not name — these fail the round trip."""
    return frozenset(observed) - declared_aspects(source, target)


def stale(source: str, target: str, observed: set[str]) -> frozenset[str]:
    """Declared losses that did not happen — reported, not failed."""
    return declared_aspects(source, target) - frozenset(observed)


def known_pairs() -> list[tuple[str, str]]:
    return sorted(DECLARED_LOSSES)


def render_markdown() -> str:
    lines: list[str] = [
        "<!-- Generated by `" + GENERATOR + "`. Do not edit by hand. -->",
        "",
        "Memories move between backends by exporting from one and importing into the",
        "other (`memcp.migrate`). This document records what that trip does not carry,",
        "for every pair the conformance round trip covers.",
        "",
        "The conformance round trip asserts against this list: a loss that happens but",
        "is not named here fails CI. That is the point of writing it down — the",
        "portability claim is only worth something if its limits are enforced.",
        "",
        "Aspects compared per memory: " + ", ".join(f"`{a}`" for a in ASPECTS) + ".",
        "",
        "## Pairs",
        "",
    ]
    for source, target in known_pairs():
        lines.append(f"### `{source}` → `{target}`")
        lines.append("")
        lines.append("| Does not survive | Why |")
        lines.append("| --- | --- |")
        for loss in sorted(pair_losses(source, target), key=lambda x: x.aspect):
            lines.append(f"| `{loss.aspect}` | {loss.reason} |")
        lines.append("")
        survives = [a for a in ASPECTS if a not in declared_aspects(source, target)]
        lines.append("Survives: " + ", ".join(f"`{a}`" for a in survives) + ".")
        lines.append("")
        notes = PAIR_NOTES.get((source, target), ())
        if notes:
            lines.append("Notes, not asserted:")
            lines.append("")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")
    lines.extend(
        [
            "## Adding a backend",
            "",
            "A round trip for a pair with no entry above fails with",
            "`UndocumentedPairError`. Add the pair to",
            "`memcp/conformance/portability.py`, regenerate this file with",
            f"`{GENERATOR}`, and the suite will hold the new pair to it.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or check docs/portability.md")
    parser.add_argument("--write", action="store_true", help=f"write {DOC_PATH} in place")
    parser.add_argument("--path", default=str(DOC_PATH), help=f"target path (default {DOC_PATH})")
    args = parser.parse_args(argv)
    text = render_markdown()
    if args.write:
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {path}")
        return 0
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
