"""Bounded, primary-first DEM merge on the primary raster's pixel lattice."""

import os
from collections.abc import Callable
from math import ceil, floor
from pathlib import Path
from threading import Event
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from highway_drainage.application.terrain import ImportCancelled


def combine_rasters(
    primary: Path,
    filler: Path,
    output: Path,
    cancel: Event,
    progress: Callable[[str], None],
    *,
    overwrite: bool = False,
) -> tuple[int, int]:
    """Preserve valid primary cells; fill masked/nonfinite cells. Z units must match."""
    primary, filler, output = (p.resolve() for p in (primary, filler, output))
    if primary == filler or output in (primary, filler):
        raise ValueError("Choose two different inputs and a separate output file.")
    if output.exists() and not overwrite:
        raise ValueError("Output already exists.")
    temporary = output.with_name(f".{output.stem}-{uuid4().hex}.tif")
    kept = filled = 0
    try:
        with rasterio.open(primary) as a, rasterio.open(filler) as b:
            for src in (a, b):
                if src.count != 1 or src.crs is None:
                    raise ValueError("Each DEM must have one elevation band and a defined CRS.")
                if src.scales != (1.0,) or src.offsets != (0.0,):
                    raise ValueError(
                        "Convert scaled elevations to physical elevation values first."
                    )
            if a.units[0] and b.units[0] and a.units[0] != b.units[0]:
                raise ValueError("Elevation units differ. Convert them before combining.")
            t = a.transform
            if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
                raise ValueError("The primary DEM must use a north-up, unrotated grid.")
            left, bottom, right, top = transform_bounds(b.crs, a.crs, *b.bounds)
            col0 = min(0, floor((left - t.c) / t.a))
            col1 = max(a.width, ceil((right - t.c) / t.a))
            row0 = min(0, floor((top - t.f) / t.e))
            row1 = max(a.height, ceil((bottom - t.f) / t.e))
            width, height = col1 - col0, row1 - row0
            if width * height > 100_000_000:
                raise ValueError("Combined extent exceeds the 100 million cell limit.")
            transform = t * rasterio.Affine.translation(col0, row0)
            options = dict(
                crs=a.crs,
                transform=transform,
                width=width,
                height=height,
                dtype="float64",
                nodata=float("nan"),
            )
            progress(f"Combining rasters: {width:,} × {height:,} cells")
            with (
                WarpedVRT(a, **options, resampling=Resampling.nearest) as av,
                WarpedVRT(b, **options, resampling=Resampling.bilinear) as bv,
                rasterio.open(
                    temporary,
                    "w",
                    driver="GTiff",
                    count=1,
                    **options,
                    tiled=True,
                    blockxsize=256,
                    blockysize=256,
                    compress="deflate",
                    predictor=3,
                    BIGTIFF="IF_SAFER",
                ) as dst,
            ):
                dst.update_tags(
                    primary_dem=str(primary),
                    gap_filler_dem=str(filler),
                    merge_rule="valid primary first; otherwise gap filler",
                )
                if a.units[0]:
                    dst.set_band_unit(1, a.units[0])
                for index, (_, window) in enumerate(dst.block_windows(1)):
                    if cancel.is_set():
                        raise ImportCancelled()
                    first = av.read(1, window=window, masked=True)
                    second = bv.read(1, window=window, masked=True)
                    valid = ~np.ma.getmaskarray(first) & np.isfinite(first.data)
                    fallback = ~valid & ~np.ma.getmaskarray(second) & np.isfinite(second.data)
                    values = np.full(first.shape, np.nan, dtype="float64")
                    values[valid] = first.data[valid]
                    values[fallback] = second.data[fallback]
                    kept += int(valid.sum())
                    filled += int(fallback.sum())
                    dst.write(values, 1, window=window)
                    if index % 100 == 0:
                        progress(f"Combined {kept:,} primary and {filled:,} gap-filler cells")
            if cancel.is_set():
                raise ImportCancelled()
            if not kept + filled:
                raise ValueError("Neither DEM contains valid elevations.")
        if overwrite:
            os.replace(temporary, output)
        else:
            # On Windows rename fails if a destination appeared during processing.
            os.rename(temporary, output)
        return kept, filled
    finally:
        temporary.unlink(missing_ok=True)
