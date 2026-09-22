"""Two disagreeing input surfaces with analytically known elevations."""

from pathlib import Path

from highway_drainage.domain.terrain import LineRole, TerrainSource
from tests.support.terrain_input import document, save


def combined_sources(tmp_path: Path, *, contours: bool = False) -> tuple[TerrainSource, ...]:
    faces, lines = document(), document()
    faces.modelspace().add_3dface([(0, 0, 0), (2, 0, 0), (2, 4, 0)])
    faces.modelspace().add_3dface([(0, 0, 0), (2, 4, 0), (0, 4, 0)])
    for y in (0, 4):
        lines.modelspace().add_polyline3d([
            (0, y, 100 + 2 * y), (4, y, 100 + (0 if contours else 4) + 2 * y),
        ])
    return (
        save(faces, tmp_path / "faces.dxf"),
        save(lines, tmp_path / "lines.dxf",
             LineRole.CONTOUR if contours else LineRole.TERRAIN_SAMPLES),
    )
