"""Load bounded display data once when engineering artifacts become available."""

import math
from pathlib import Path
from threading import Event

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import HydrologyResult
from highway_drainage.domain.preview import BoundaryPreview, CatchmentOutline, RasterPreview


class RasterPreviewReader:
    def raster(self, path: Path, cancel: Event) -> RasterPreview:
        if cancel.is_set():
            raise ImportCancelled()
        with rasterio.Env(GDAL_CACHEMAX=16 * 1024**2), rasterio.open(path) as src:
            if not all(math.isfinite(v) for v in src.transform[:6]):
                raise ValueError("Cannot display a raster with nonfinite affine coordinates.")
            rows, columns = src.block_shapes[0]
            if rows * columns * np.dtype(src.dtypes[0]).itemsize > 64 * 1024**2:
                raise ValueError("Raster storage block exceeds 64 MiB; retile for preview.")
            scale = min(1.0, 768 / max(src.width, src.height))
            width, height = max(1, round(src.width * scale)), max(1, round(src.height * scale))
            data = src.read(
                1, out_shape=(height, width), masked=True, resampling=Resampling.nearest
            )
            valid = ~np.ma.getmaskarray(data) & np.isfinite(data.data)
            if src.nodata is not None:
                valid &= data.data != src.nodata
            low, high = (
                (float(data.data[valid].min()), float(data.data[valid].max()))
                if valid.any()
                else (0, 0)
            )
            normalized = np.zeros((height, width), dtype="float64")
            normalized[valid] = (data.data[valid] - low) / (high - low) if high > low else 0.5
            rgba = np.zeros((height, width, 4), dtype="uint8")
            rgba[:, :, 0] = 40 + normalized * 195
            rgba[:, :, 1] = 80 + normalized * 155
            rgba[:, :, 2] = 105 + normalized * 120
            rgba[:, :, 3] = valid * 255
            t = src.transform
            affine = (
                t.a * src.width / width,
                t.b * src.height / height,
                t.c,
                t.d * src.width / width,
                t.e * src.height / height,
                t.f,
            )
            information = (
                f"{path.name}\nCRS: {src.crs or 'unspecified'}; "
                f"{src.width:,} x {src.height:,} pixels; pixel steps: {t.a:g}, {t.e:g}\n"
                f"Bounds: {tuple(src.bounds)}\n"
                f"Sampled elevation: {low:g}–{high:g} {src.units[0] or '(units unspecified)'}; "
                f"NoData: {src.nodata}. Preview {width} x {height}; transparent = NoData."
            )
        if cancel.is_set():
            raise ImportCancelled()
        return RasterPreview(path, width, height, rgba.tobytes(), affine, information)

    def boundaries(self, result: HydrologyResult, cancel: Event) -> BoundaryPreview:
        outlines: list[CatchmentOutline] = []
        vertices, cells = 0, 0
        with rasterio.Env(GDAL_CACHEMAX=16 * 1024**2):
            for catchment in result.catchments:
                if cancel.is_set():
                    raise ImportCancelled()
                if catchment.mask is None:
                    continue
                with rasterio.open(catchment.mask) as src:
                    cells += src.width * src.height
                    if src.width * src.height > 2_000_000 or cells > 100_000_000:
                        return BoundaryPreview(
                            tuple(outlines),
                            "Boundary preview limit reached; remaining masks omitted.",
                        )
                    mask = src.read(1) == 1
                    rings: list[tuple[tuple[float, float], ...]] = []
                    for geometry, _ in shapes(
                        mask.astype("uint8"), mask=mask, transform=src.transform
                    ):
                        if cancel.is_set():
                            raise ImportCancelled()
                        for ring in geometry["coordinates"]:
                            vertices += len(ring)
                            if vertices > 250_000:
                                return BoundaryPreview(
                                    tuple(outlines),
                                    "Boundary vertex limit reached; remaining masks omitted.",
                                )
                            rings.append(tuple((float(x), float(y)) for x, y in ring))
                    outlines.append(
                        CatchmentOutline(catchment.outlet.original.point.identifier, tuple(rings))
                    )
        return BoundaryPreview(tuple(outlines))
