"""Overlapping face fixtures shared by adapter and GUI tests."""

from pathlib import Path

from highway_drainage.domain.terrain import TerrainDataset
from tests.support.terrain_input import document, load, save


def overlapping(tmp_path: Path, z: float = 0) -> TerrainDataset:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 10), (4, 0, 14), (0, 4, 18)])
    doc.modelspace().add_3dface([(1, 0, 11 + z), (5, 0, 15 + z), (1, 4, 19 + z)])
    return load(save(doc, tmp_path / "overlap.dxf"))
