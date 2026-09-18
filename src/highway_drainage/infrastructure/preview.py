"""Load bounded display data once when engineering artifacts become available."""

import math
from pathlib import Path
from threading import Event

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.windows import Window

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
        count = sum(c.mask is not None for c in result.catchments)
        # Share the display vertex budget across outlets, rather than omitting later ones.
        if count > 50_000:
            return BoundaryPreview((), "Too many catchments for boundary preview (limit 50,000).")
        budget = 250_000 // max(1, count)
        simplified = 0
        with rasterio.Env(GDAL_CACHEMAX=16 * 1024**2):
            for catchment in result.catchments:
                if cancel.is_set():
                    raise ImportCancelled()
                if catchment.mask is None:
                    continue
                with rasterio.open(catchment.mask) as src:
                    factor = (
                        math.ceil(math.sqrt(src.width * src.height / 262_144))
                        if src.width * src.height > 2_000_000
                        else 1
                    )
                    # Any contributing source cell keeps its display cell visible, even
                    # for a tiny catchment. NoData (255) is never treated as membership.
                    mask = np.zeros(
                        (math.ceil(src.height / factor), math.ceil(src.width / factor)),
                        dtype="bool",
                    )
                    rows = max(1, 1_048_576 // src.width // factor) * factor
                    for top in range(0, src.height, rows):
                        if cancel.is_set():
                            raise ImportCancelled()
                        data = (
                            src.read(
                                1, window=Window(0, top, src.width, min(rows, src.height - top))
                            )
                            == 1
                        )
                        reduced = _aggregate_mask(data, factor)
                        mask[top // factor : top // factor + reduced.shape[0]] = reduced
                    while True:
                        rings = _mask_rings(mask, factor, src, budget, cancel)
                        if rings is not None:
                            break
                        mask = _aggregate_mask(mask, 2)
                        factor *= 2
                    simplified += factor > 1
                    outlines.append(
                        CatchmentOutline(catchment.outlet.original.point.identifier, tuple(rings))
                    )
        diagnostic = (
            f"Simplified display boundaries for {simplified} catchments; small holes or gaps "
            "may disappear. Exported catchment masks remain at full resolution."
            if simplified
            else "Full-resolution catchment boundaries."
        )
        return BoundaryPreview(tuple(outlines), diagnostic)


def _aggregate_mask(mask: npt.NDArray[np.bool_], factor: int) -> npt.NDArray[np.bool_]:
    if factor == 1:
        return mask
    columns = np.logical_or.reduceat(mask, np.arange(0, mask.shape[1], factor), axis=1)
    return np.logical_or.reduceat(columns, np.arange(0, mask.shape[0], factor), axis=0)


def _mask_rings(
    mask: npt.NDArray[np.bool_],
    factor: int,
    source: rasterio.io.DatasetReader,
    budget: int,
    cancel: Event,
) -> list[tuple[tuple[float, float], ...]] | None:
    rings: list[tuple[tuple[float, float], ...]] = []
    vertices = 0
    t = source.transform
    for geometry, _ in shapes(mask.astype("uint8"), mask=mask):
        if cancel.is_set():
            raise ImportCancelled()
        for ring in geometry["coordinates"]:
            vertices += len(ring)
            if vertices > budget:
                return None
            # Clip partial edge cells to the original raster footprint before mapping
            # to world coordinates; this also handles rotated affine transforms.
            pixels = (
                (min(x * factor, source.width), min(y * factor, source.height)) for x, y in ring
            )
            rings.append(
                tuple((t.a * x + t.b * y + t.c, t.d * x + t.e * y + t.f) for x, y in pixels)
            )
    return rings
