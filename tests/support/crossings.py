"""Reusable synthetic builders; no test functions."""

from pathlib import Path

from ezdxf.document import Drawing

from highway_drainage.application.crossings import FindCrossings
from highway_drainage.domain.crossings import CrossingRequest, LineSource
from highway_drainage.infrastructure.cad_lines import CadLineReader
from highway_drainage.infrastructure.crossings import ShapelyCrossings


def crossing_service() -> FindCrossings:
    return FindCrossings(CadLineReader(), ShapelyCrossings())


def request_for(tmp_path: Path, highway: Drawing, culvert: Drawing) -> CrossingRequest:
    a, b = tmp_path / "highway.dxf", tmp_path / "culverts.dxf"
    highway.saveas(a)
    culvert.saveas(b)
    return CrossingRequest(LineSource(a, "EPSG:32634"), LineSource(b, "EPSG:32634"), "EPSG:32634")
