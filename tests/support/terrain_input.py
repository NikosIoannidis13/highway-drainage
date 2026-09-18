"""Reusable synthetic builders; no test functions."""

from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new

from highway_drainage.application.terrain import ImportTerrain
from highway_drainage.domain.terrain import (
    LineRole,
    TerrainDataset,
    TerrainRequest,
    TerrainSource,
)
from highway_drainage.infrastructure.dxf import DxfTerrainReader
from highway_drainage.infrastructure.terrain import TerrainNormalizer

TRIANGLE = [(500000.0, 4200000.0, 10.0), (500010.0, 4200000.0, 11.0), (500000.0, 4200010.0, 12.0)]


def document() -> Drawing:
    doc = new()
    doc.units = 6
    return doc


def save(doc: Drawing, path: Path, role: LineRole = LineRole.UNASSIGNED) -> TerrainSource:
    doc.saveas(path)
    return TerrainSource(path, "EPSG:32634", "survey datum", default_role=role)


def service() -> ImportTerrain:
    return ImportTerrain(DxfTerrainReader(), TerrainNormalizer())


def load(*sources: TerrainSource) -> TerrainDataset:
    return service().execute(TerrainRequest(sources, "EPSG:32634", "survey datum"))
