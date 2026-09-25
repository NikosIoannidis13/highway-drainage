from pathlib import Path

import pytest

from highway_drainage.application.point_review import PointReview, inlet_candidates
from highway_drainage.domain.crossings import (
    CadLine,
    CadReference,
    CrossingPoint,
    CrossingResult,
    PointRole,
)


def result() -> CrossingResult:
    ref = CadReference(Path("culvert.dxf"), "A", "pipes", "LINE")
    return CrossingResult(
        "EPSG:2100",
        (),
        (CadLine(ref, ((0, 0), (10, 0))),),
        tuple(CrossingPoint(str(i), float(i), 0, ref, (), False, False) for i in range(4)),
        (),
        0.05,
    )


def test_group_assignment_filter_clear_and_undo_preserve_points() -> None:
    original = result()
    review = PointReview(original)
    with pytest.raises(ValueError, match="Mark at least one"):
        inlet_candidates(review.result)
    review.assign({"0", "1"}, PointRole.INLET)
    review.assign({"2"}, PointRole.OUTLET)
    assert [p.identifier for p in inlet_candidates(review.result).points] == ["0", "1"]
    assert len(review.result.points) == 4
    review.assign({"0"}, PointRole.UNCLASSIFIED)
    assert len(inlet_candidates(review.result).points) == 1
    review.undo()
    assert len(inlet_candidates(review.result).points) == 2
    review.undo()
    review.undo()
    assert review.result == original
    assert not review.can_undo
    review.assign(set(), PointRole.INLET)
    assert not review.can_undo


def test_manual_inlet_has_real_culvert_reference_and_undo_removes_it() -> None:
    review = PointReview(result())
    ref = review.result.culverts[0].reference
    with pytest.raises(ValueError, match="Choose the culvert"):
        review.add_inlet(5, 6, "")
    with pytest.raises(ValueError, match="finite"):
        review.add_inlet(float("nan"), 0, ref.identifier)
    identifier = review.add_inlet(5, 6, ref.identifier)
    (point,) = inlet_candidates(review.result).points
    assert (point.x, point.y) == (5, 6)
    assert point.manual and point.culvert == ref and not point.highways
    review.undo()
    assert len(review.result.points) == 4
    assert review.add_inlet(6, 7, ref.identifier) != identifier
