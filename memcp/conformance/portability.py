"""What a cross-backend round trip loses, declared per backend pair.

For the backends this package ships, `DECLARED_LOSSES` below is the single source of
truth. `docs/portability.md` is generated from it
(`python -m memcp.conformance.portability --write`) and a test asserts the file
matches, so the document cannot drift from what the round trip enforces.

The round trip asserts the observed losses for a pair equal its declared set,
exactly. An undeclared loss fails — that is the point of writing them down. So does
a declared loss that no longer happens: a document claiming a field is lost would
quietly license a real regression in that field, so an over-claim is a failure too.
`content` and `scope` can never be declared lost at all.

An adapter outside this repository cannot edit this file — it has it installed under
site-packages. Such an adapter calls `declare_pair()` at import time to register its
own pairs; see `docs/conformance.md`. Registered pairs are enforced exactly like the
built-in ones but are not written into this repository's `docs/portability.md`,
because publishing them is the adapter's own job. `render_markdown(pairs=...)` will
generate that document for them.
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
# import calls add(), which mints a new id and stamps a new created_at. Public,
# because they are true of any pair and an out-of-tree adapter should reuse them
# rather than retype them.
IDENTITY_LOSSES: tuple[Loss, ...] = (
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
    ("in_memory", "in_memory"): IDENTITY_LOSSES,
    ("in_memory", "sqlite"): IDENTITY_LOSSES,
    ("in_memory", "mem0"): IDENTITY_LOSSES,
    ("sqlite", "in_memory"): IDENTITY_LOSSES,
    ("sqlite", "sqlite"): IDENTITY_LOSSES,
    ("sqlite", "mem0"): IDENTITY_LOSSES,
    ("mem0", "in_memory"): IDENTITY_LOSSES,
    ("mem0", "sqlite"): IDENTITY_LOSSES,
    ("mem0", "mem0"): IDENTITY_LOSSES,
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
    ("sqlite", "mem0"): (
        "mem0 stores a content hash and may deduplicate server-side. Import writes "
        "with infer=false, so nothing is re-extracted, but a target that already "
        "holds the same content in the same scope can collapse the write.",
    ),
    ("mem0", "sqlite"): (
        "sqlite search is word-overlap, not vector similarity — the same scoring "
        "in_memory uses. Content and scope survive; result order does not.",
    ),
    ("in_memory", "sqlite"): (
        "The two backends score identically, so this pair is a durability change "
        "rather than a retrieval change: the same query returns the same order.",
    ),
    ("sqlite", "in_memory"): (
        "The reverse of the same pair, and the way to read a deployment's SQLite "
        "file into a throwaway process. Everything but identity survives.",
    ),
}


# Pairs registered at runtime by adapters outside this package. Kept apart from
# DECLARED_LOSSES so this repository's generated document stays a function of this
# repository's source alone.
_REGISTERED_LOSSES: dict[tuple[str, str], tuple[Loss, ...]] = {}
_REGISTERED_NOTES: dict[tuple[str, str], tuple[str, ...]] = {}


class UndocumentedPairError(KeyError):
    """Raised when a round trip runs for a pair nothing has a record for."""


def declare_pair(
    source: str,
    target: str,
    losses: tuple[Loss, ...] | list[Loss],
    *,
    notes: tuple[str, ...] = (),
) -> None:
    """Register the portability record for a pair this package does not ship.

    Call it at import time from the module `MEMCP_CONFORMANCE_EXTRA` names, which the
    suite imports before it collects. Held to the same bar as a built-in pair: the
    round trip asserts the observed losses equal `losses` exactly, and `content` and
    `scope` still cannot appear.

        from memcp.conformance.portability import IDENTITY_LOSSES, declare_pair

        for pair in (("mystore", "mystore"), ("mystore", "in_memory")):
            declare_pair(*pair, IDENTITY_LOSSES)
    """
    _REGISTERED_LOSSES[(source, target)] = tuple(losses)
    if notes:
        _REGISTERED_NOTES[(source, target)] = tuple(notes)


def registered_pairs() -> list[tuple[str, str]]:
    return sorted(_REGISTERED_LOSSES)


def pair_losses(source: str, target: str) -> tuple[Loss, ...]:
    record = DECLARED_LOSSES.get((source, target)) or _REGISTERED_LOSSES.get((source, target))
    if record is None:
        raise UndocumentedPairError(
            f"No portability record for {source} -> {target}. A backend shipped with "
            f"memcp belongs in DECLARED_LOSSES in memcp/conformance/portability.py, "
            f"regenerated into {DOC_PATH} with `{GENERATOR}`. An adapter outside memcp "
            f"calls declare_pair({source!r}, {target!r}, IDENTITY_LOSSES) at import "
            f"time — see docs/conformance.md."
        )
    return record


def pair_notes(source: str, target: str) -> tuple[str, ...]:
    return PAIR_NOTES.get((source, target)) or _REGISTERED_NOTES.get((source, target), ())


def declared_aspects(source: str, target: str) -> frozenset[str]:
    return frozenset(loss.aspect for loss in pair_losses(source, target))


def undeclared(source: str, target: str, observed: set[str]) -> frozenset[str]:
    """Observed losses no record names — these fail the round trip."""
    return frozenset(observed) - declared_aspects(source, target)


def stale(
    source: str,
    target: str,
    observed: set[str],
    *,
    observable: set[str] | None = None,
) -> frozenset[str]:
    """Declared losses that did not happen — these fail the round trip too.

    A record claiming a field is lost when it survives would license a real
    regression in that field, so an over-claim is as much a defect as a silent loss.

    `observable` narrows this to aspects the run could actually measure. `history` is
    only comparable when both backends declare `memory_history`; without the narrowing
    a correct declaration would fail purely because the pair cannot check it. What was
    declared but not measurable comes back from `unverified()` instead, so it is
    reported rather than quietly excused.
    """
    declared = declared_aspects(source, target)
    if observable is not None:
        declared &= frozenset(observable)
    return declared - frozenset(observed)


def unverified(source: str, target: str, observable: set[str]) -> frozenset[str]:
    """Declared losses this pair had no way to observe."""
    return declared_aspects(source, target) - frozenset(observable)


def known_pairs() -> list[tuple[str, str]]:
    return sorted(DECLARED_LOSSES)


def render_markdown(pairs: list[tuple[str, str]] | None = None) -> str:
    """Render the portability document.

    Defaults to the pairs this package ships, which is what keeps
    `docs/portability.md` a function of this repository's source. An out-of-tree
    adapter passes `registered_pairs()` to generate the same document for its own.
    """
    lines: list[str] = [
        "<!-- Generated by `" + GENERATOR + "`. Do not edit by hand. -->",
        "",
        "Memories move between backends by exporting from one and importing into the",
        "other (`memcp.migrate`). This document records what that trip does not carry,",
        "for every pair the conformance round trip covers.",
        "",
        "The conformance round trip asserts against this list, exactly: a loss that",
        "happens but is not named here fails CI, and so does a loss named here that no",
        "longer happens. That is the point of writing it down — the portability claim is",
        "only worth something if its limits are enforced in both directions.",
        "",
        "Aspects compared per memory: " + ", ".join(f"`{a}`" for a in ASPECTS) + ".",
        "",
        "## Pairs",
        "",
    ]
    for source, target in known_pairs() if pairs is None else pairs:
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
        notes = pair_notes(source, target)
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
            "`UndocumentedPairError`. For a backend shipped with memcp, add the pair to",
            "`DECLARED_LOSSES` in `memcp/conformance/portability.py` and regenerate this",
            f"file with `{GENERATOR}`. For an adapter outside memcp, call",
            "`declare_pair()` at import time — `docs/conformance.md` has the recipe.",
            "Either way the suite then holds the new pair to what was declared.",
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
