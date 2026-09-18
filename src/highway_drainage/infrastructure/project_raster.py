"""Read raster metadata and a bounded preview without altering the source file."""

from math import isfinite
from pathlib import Path
from threading import Event

import rasterio

from highway_drainage.application.project_raster import ProjectRaster
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.infrastructure.preview import RasterPreviewReader
from highway_drainage.infrastructure.terrain import _projected_metres


class RasterProjectReader:
    def read(self, path: Path, cancel: Event) -> ProjectRaster:
        if cancel.is_set():
            raise ImportCancelled()
        with rasterio.open(path) as src:
            if src.crs is None:
                raise ValueError("Raster has no CRS. Assign its actual CRS in GIS before loading.")
            crs = _projected_metres(src.crs.to_wkt())
            if src.count != 1:
                raise ValueError("Choose a single-band elevation GeoTIFF, not an RGB image.")
            t = src.transform
            if not all(isfinite(v) for v in t[:6]) or t.a * t.e - t.b * t.d == 0:
                raise ValueError("Raster affine transform is invalid.")
            label = f"{crs.to_string()} — {crs.name} (metres)"
        preview = RasterPreviewReader().raster(path, cancel)
        return ProjectRaster(path, crs.to_wkt(), label, preview)
