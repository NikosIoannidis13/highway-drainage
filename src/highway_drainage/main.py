"""Application entry point and future composition root."""

import sys

from PySide6.QtWidgets import QApplication

from highway_drainage.application.coordinates import ValidateCoordinates
from highway_drainage.application.crossings import FindCrossings
from highway_drainage.application.dem import GenerateDem
from highway_drainage.application.hydrology import DelineateCatchments
from highway_drainage.application.outlets import SelectOutlets
from highway_drainage.application.terrain import ImportTerrain
from highway_drainage.infrastructure.cad_lines import CadLineReader
from highway_drainage.infrastructure.coordinates import RasterCoordinateInspector
from highway_drainage.infrastructure.crossings import ShapelyCrossings
from highway_drainage.infrastructure.dxf import DxfTerrainReader
from highway_drainage.infrastructure.hydrology import PyFlwdirHydrology
from highway_drainage.infrastructure.outlets import RasterOutletSnapper
from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.infrastructure.raster import GeoTiffWriter
from highway_drainage.infrastructure.surface import SurfaceBuilder
from highway_drainage.infrastructure.terrain import TerrainNormalizer
from highway_drainage.presentation.main_window import MainWindow


def main() -> int:
    """Create the GUI and run its event loop."""
    app = QApplication(sys.argv)
    app.setApplicationName("Highway Drainage")
    validator = ValidateCoordinates(RasterCoordinateInspector())
    window = MainWindow(
        ImportTerrain(DxfTerrainReader(), TerrainNormalizer()),
        dem_use_case=GenerateDem(SurfaceBuilder(), GeoTiffWriter()),
        crossing_use_case=FindCrossings(CadLineReader(), ShapelyCrossings()),
        coordinate_use_case=validator,
        outlet_use_case=SelectOutlets(validator, RasterOutletSnapper()),
        hydrology_use_case=DelineateCatchments(PyFlwdirHydrology()),
        previews=RasterPreviewReader(),
    )
    window.show()
    return app.exec()
