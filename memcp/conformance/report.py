"""Per-capability pass/skip accounting for a conformance run (A1).

The suite records outcomes here; the pytest plugin renders them as a table after
the run and, with --conformance-report, as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memcp.conformance.capabilities import ALL_CAPABILITIES, REQUIRED_METHODS

REQUIRED_GROUP = "(required methods)"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _names(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


@dataclass
class Outcome:
    """Aggregated result for one (backend, capability) cell."""

    backend: str
    capability: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failed:
            return FAIL
        if self.passed:
            return PASS
        return SKIP

    @property
    def detail(self) -> str:
        if self.status == SKIP and self.notes:
            return self.notes[0]
        counts = f"{self.passed} passed"
        if self.failed:
            counts += f", {self.failed} failed"
        if self.skipped:
            counts += f", {self.skipped} skipped"
        return counts


@dataclass
class RoundTrip:
    source: str
    target: str
    exported: int
    imported: int
    observed_losses: list[str]
    declared_losses: list[str]
    stale_losses: list[str]
    ran: bool = True
    skip_reason: str | None = None


class Recorder:
    """Collects what a conformance run learned about each backend."""

    def __init__(self) -> None:
        self.declared: dict[str, set[str]] = {}
        self.unavailable: dict[str, str] = {}
        self.outcomes: dict[tuple[str, str], Outcome] = {}
        self.round_trips: list[RoundTrip] = []

    # --- recording ---

    def record_declared(self, backend: str, capabilities: set[str]) -> None:
        self.declared[backend] = set(capabilities)

    def record_unavailable(self, backend: str, reason: str) -> None:
        self.unavailable[backend] = reason

    def record(self, backend: str, capability: str, status: str, note: str = "") -> None:
        cell = self.outcomes.setdefault(
            (backend, capability), Outcome(backend=backend, capability=capability)
        )
        if status == PASS:
            cell.passed += 1
        elif status == FAIL:
            cell.failed += 1
        else:
            cell.skipped += 1
        if note and note not in cell.notes:
            cell.notes.append(note)

    def record_round_trip(self, trip: RoundTrip) -> None:
        self.round_trips.append(trip)

    # --- rendering ---

    def backends(self) -> list[str]:
        names = set(self.declared) | set(self.unavailable) | {b for b, _ in self.outcomes}
        return sorted(names)

    def rows(self) -> list[tuple[str, str, str, str]]:
        """(backend, capability, status, detail) for every backend/capability cell."""
        rows: list[tuple[str, str, str, str]] = []
        for backend in self.backends():
            if backend in self.unavailable:
                rows.append(
                    (backend, REQUIRED_GROUP, SKIP, f"unavailable: {self.unavailable[backend]}")
                )
                continue
            required = self.outcomes.get((backend, REQUIRED_GROUP))
            rows.append(
                (
                    backend,
                    REQUIRED_GROUP,
                    required.status if required else SKIP,
                    required.detail if required else "not exercised",
                )
            )
            declared = self.declared.get(backend)
            for cap in ALL_CAPABILITIES:
                cell = self.outcomes.get((backend, cap))
                if cell is not None and cell.status != SKIP:
                    rows.append((backend, cap, cell.status, cell.detail))
                    continue
                if declared is not None and cap not in declared:
                    rows.append((backend, cap, SKIP, "not declared in capabilities()"))
                elif cell is not None:
                    rows.append((backend, cap, SKIP, cell.detail))
                else:
                    rows.append((backend, cap, SKIP, "not exercised"))
            extra = sorted(
                c
                for b, c in self.outcomes
                if b == backend and c != REQUIRED_GROUP and c not in ALL_CAPABILITIES
            )
            for name in extra:
                cell = self.outcomes[(backend, name)]
                rows.append((backend, name, cell.status, cell.detail))
        return rows

    def render_text(self) -> list[str]:
        rows = self.rows()
        lines = ["memcp backend conformance", "=" * 25, ""]
        if not rows:
            lines.append("No conformance tests ran.")
            return lines
        widths = [
            max(len(r[i]) for r in [("backend", "capability", "status", "detail"), *rows])
            for i in range(4)
        ]
        header = ("backend", "capability", "status", "detail")
        lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
        lines.append("  ".join("-" * widths[i] for i in range(4)))
        for row in rows:
            lines.append("  ".join(row[i].ljust(widths[i]) for i in range(4)))
        lines.append("")
        for backend in self.backends():
            declared = self.declared.get(backend)
            if declared is None:
                continue
            missing = sorted(set(ALL_CAPABILITIES) - declared)
            lines.append(
                f"{backend}: implements {_names(sorted(declared))}. "
                f"Not implemented: {_names(missing)}."
            )
        if self.round_trips:
            lines.append("")
            lines.append("round trips")
            lines.append("-" * 11)
            for t in self.round_trips:
                if not t.ran:
                    lines.append(f"{t.source} -> {t.target}: skipped ({t.skip_reason})")
                    continue
                lines.append(
                    f"{t.source} -> {t.target}: {t.exported} exported, {t.imported} imported; "
                    f"documented losses {t.declared_losses}"
                    + (f"; stale {t.stale_losses}" if t.stale_losses else "")
                    + (f"; UNDOCUMENTED {t.observed_losses}" if t.observed_losses else "")
                )
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_methods": list(REQUIRED_METHODS),
            "capabilities": list(ALL_CAPABILITIES),
            "backends": {
                backend: {
                    "available": backend not in self.unavailable,
                    "unavailable_reason": self.unavailable.get(backend),
                    "declared_capabilities": sorted(self.declared.get(backend, [])),
                    "not_implemented": sorted(
                        set(ALL_CAPABILITIES) - self.declared.get(backend, set())
                    )
                    if backend in self.declared
                    else None,
                }
                for backend in self.backends()
            },
            "results": [
                {"backend": b, "capability": c, "status": s, "detail": d}
                for b, c, s, d in self.rows()
            ],
            "round_trips": [
                {
                    "source": t.source,
                    "target": t.target,
                    "ran": t.ran,
                    "skip_reason": t.skip_reason,
                    "exported": t.exported,
                    "imported": t.imported,
                    "documented_losses": t.declared_losses,
                    "undocumented_losses": t.observed_losses,
                    "stale_declarations": t.stale_losses,
                }
                for t in self.round_trips
            ],
        }
