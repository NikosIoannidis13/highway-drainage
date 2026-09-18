"""Indexed segment intersections; retain culvert identity and all highway matches."""

from dataclasses import dataclass, field
from threading import Event

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import (
    XY,
    CadLine,
    CrossingIssue,
    CrossingPoint,
    CrossingRequest,
    CrossingResult,
    ExtractedLines,
)
from highway_drainage.infrastructure.cad_lines import CrossingLimitError


@dataclass
class _PairHits:
    points: set[XY] = field(default_factory=set)
    overlaps: list[LineString] = field(default_factory=list)


def _endpoint(line: CadLine, point: XY) -> bool:
    return line.vertices[0] != line.vertices[-1] and point in (line.vertices[0], line.vertices[-1])


class ShapelyCrossings:
    def intersect(
        self,
        highways: ExtractedLines,
        culverts: ExtractedLines,
        request: CrossingRequest,
        cancel: Event,
    ) -> CrossingResult:
        if highways.crs_wkt != culverts.crs_wkt:
            raise ValueError("Highway and culvert geometry must be normalized to the same CRS.")
        issues = list((*highways.issues, *culverts.issues))
        road_segments: list[LineString] = []
        owners: list[int] = []
        for index, line in enumerate(highways.lines):
            if cancel.is_set():
                raise ImportCancelled()
            for a, b in zip(line.vertices, line.vertices[1:], strict=False):
                if a != b:
                    road_segments.append(LineString((a, b)))
                    owners.append(index)
        tree = STRtree(road_segments)
        pairs: dict[tuple[int, int], _PairHits] = {}
        checked, hits = 0, 0
        for ci, culvert in enumerate(culverts.lines):
            for a, b in zip(culvert.vertices, culvert.vertices[1:], strict=False):
                if cancel.is_set():
                    raise ImportCancelled()
                if a == b:
                    continue
                segment = LineString((a, b))
                candidates = tree.query(segment)
                checked += len(candidates)
                if checked > request.limits.max_candidate_pairs:
                    raise CrossingLimitError(
                        f"More than {request.limits.max_candidate_pairs:,} segment pairs. "
                        "Filter relevant layers, simplify curves or split the study area."
                    )
                for raw_index in candidates:
                    if cancel.is_set():
                        raise ImportCancelled()
                    index = int(raw_index)
                    common = segment.intersection(road_segments[index])
                    if common.is_empty:
                        continue
                    hits += 1
                    if hits > request.limits.max_hits:
                        raise CrossingLimitError(
                            "Intersection workload limit exceeded. Filter/split CAD inputs."
                        )
                    pair = pairs.setdefault((ci, owners[index]), _PairHits())
                    if isinstance(common, Point):
                        pair.points.add((float(common.x), float(common.y)))
                    elif isinstance(common, LineString):
                        pair.overlaps.append(common)
                    else:
                        raise ValueError(
                            "Unexpected segment intersection geometry; review the input."
                        )
        grouped: dict[tuple[int, float, float], set[int]] = {}
        for (ci, hi), pair in sorted(pairs.items()):
            if cancel.is_set():
                raise ImportCancelled()
            overlap_tree = STRtree(pair.overlaps) if pair.overlaps else None
            if overlap_tree is not None:
                issues.append(
                    CrossingIssue(
                        "warning",
                        "overlap",
                        "Coincident line section: no point outlet was inferred.",
                        culverts.lines[ci].reference,
                        highways.lines[hi].reference,
                    )
                )
            for x, y in pair.points:
                # Adjacent CAD segments can produce an endpoint hit on an overlapping
                # section. Suppress those too, not just the line-valued intersections.
                if overlap_tree is not None and len(
                    overlap_tree.query(Point(x, y), predicate="intersects")
                ):
                    continue
                grouped.setdefault((ci, x, y), set()).add(hi)
                if len(grouped) > request.limits.max_points:
                    raise CrossingLimitError("Too many crossing points. Filter/split the inputs.")
        points: list[CrossingPoint] = []
        for number, ((ci, x, y), matches) in enumerate(sorted(grouped.items()), start=1):
            highway_lines = [highways.lines[i] for i in sorted(matches)]
            culvert = culverts.lines[ci]
            points.append(
                CrossingPoint(
                    f"CP{number:05d}",
                    x,
                    y,
                    culvert.reference,
                    tuple(line.reference for line in highway_lines),
                    _endpoint(culvert, (x, y))
                    or any(_endpoint(line, (x, y)) for line in highway_lines),
                    culvert.approximated or any(line.approximated for line in highway_lines),
                )
            )
        if cancel.is_set():
            raise ImportCancelled()
        return CrossingResult(
            highways.crs_wkt,
            highways.lines,
            culverts.lines,
            tuple(points),
            tuple(issues),
            request.curve_tolerance,
            highways.source or request.highway,
            culverts.source or request.culverts,
        )
