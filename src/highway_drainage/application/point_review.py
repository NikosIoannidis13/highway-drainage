"""Explicit culvert point roles and undo, independent of the preview implementation."""

from dataclasses import replace
from math import isfinite

from highway_drainage.domain.crossings import CrossingPoint, CrossingResult, PointRole


def inlet_candidates(result: CrossingResult) -> CrossingResult:
    points = tuple(p for p in result.points if p.role == PointRole.INLET)
    if not points:
        raise ValueError(
            "Mark at least one culvert inlet before selecting pour points. "
            "Choose Inlet in the crossing table's Role dropdown, or select points "
            "in Drainage View and choose Mark as inlet."
        )
    return replace(result, points=points)


class PointReview:
    def __init__(self, result: CrossingResult) -> None:
        self.result = result
        self._history: list[CrossingResult] = []
        self._next_manual = 1

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    def _record(self, result: CrossingResult) -> None:
        if result != self.result:
            self._history.append(self.result)
            self._history = self._history[-50:]
            self.result = result

    def assign(self, identifiers: set[str], role: PointRole) -> None:
        self._record(
            replace(
                self.result,
                points=tuple(
                    replace(p, role=role) if p.identifier in identifiers else p
                    for p in self.result.points
                ),
            )
        )

    def add_inlet(self, x: float, y: float, culvert_identifier: str) -> str:
        if not isfinite(x) or not isfinite(y):
            raise ValueError("Inlet coordinates must be finite.")
        culvert = next(
            (c for c in self.result.culverts if c.reference.identifier == culvert_identifier), None
        )
        if culvert is None:
            raise ValueError("Choose the culvert associated with the new inlet.")
        existing = {p.identifier for p in self.result.points}
        while f"MI{self._next_manual:05d}" in existing:
            self._next_manual += 1
        identifier = f"MI{self._next_manual:05d}"
        self._next_manual += 1
        point = CrossingPoint(
            identifier,
            x,
            y,
            culvert.reference,
            (),
            False,
            False,
            PointRole.INLET,
            True,
        )
        self._record(replace(self.result, points=(*self.result.points, point)))
        return identifier

    def undo(self) -> None:
        if self._history:
            self.result = self._history.pop()
