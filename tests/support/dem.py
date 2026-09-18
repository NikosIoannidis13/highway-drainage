"""Reusable synthetic builders; no test functions."""

from pathlib import Path

from highway_drainage.application.dem import GenerateDem
from highway_drainage.domain.terrain import TerrainDataset
from highway_drainage.infrastructure.raster import GeoTiffWriter
from highway_drainage.infrastructure.surface import SurfaceBuilder
from tests.support.terrain_input import document, load, save


def dem_service() -> GenerateDem:
    return GenerateDem(SurfaceBuilder(), GeoTiffWriter())


def plane(tmp_path: Path) -> TerrainDataset:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 10), (4, 0, 14), (4, 4, 22)])
    doc.modelspace().add_3dface([(0, 0, 10), (4, 4, 22), (0, 4, 18)])
    return load(save(doc, tmp_path / "plane.dxf"))
