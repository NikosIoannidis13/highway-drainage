"""Bounded cell-centre interpolation and atomic, non-overwriting GeoTIFF export."""

import os
from collections.abc import Callable
from math import ceil, floor, isnan
from threading import Event
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window
from shapely import intersects_xy
from shapely.geometry import Polygon, box
from shapely.strtree import STRtree

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import DemRequest, GridPlan, ResourceLimitError, TerrainModel
from highway_drainage.infrastructure.surface import xy


def _pixel_bounds(
    bounds: tuple[float, float, float, float], plan: GridPlan
) -> tuple[int, int, int, int]:
    xmin, _, _, ymax = plan.extent
    left, bottom, right, top = bounds
    size = plan.cell_size
    return (
        max(0, min(plan.width, floor((left - xmin) / size))),
        max(0, min(plan.height, floor((ymax - top) / size))),
        max(0, min(plan.width, ceil((right - xmin) / size))),
        max(0, min(plan.height, ceil((ymax - bottom) / size))),
    )


class GeoTiffWriter:
    def write(
        self,
        request: DemRequest,
        model: TerrainModel,
        plan: GridPlan,
        cancel: Event,
        progress: Callable[[str], None],
    ) -> int:
        progress("Preparing DEM: checking elevation range. GeoTIFF export is still in progress.")
        if cancel.is_set():
            raise ImportCancelled()
        scale = 1.0 if request.elevation_unit == "m" else 1.0 / 0.3048
        low = min(v.z * scale for t in model.triangles for v in t)
        high = max(v.z * scale for t in model.triangles for v in t)
        if max(abs(low), abs(high)) > np.finfo(np.float32).max:
            raise ValueError("Elevation cannot be represented as Float32 in the requested units.")
        nodata = float(np.float32(request.nodata))
        if not isnan(nodata) and float(np.float32(low)) <= nodata <= float(np.float32(high)):
            raise ValueError("NoData overlaps the elevation range. Choose an outside value or NaN.")
        polygons: list[Polygon] = []
        bounds: list[tuple[int, int, int, int]] = []
        total_triangles = len(model.triangles)
        progress(f"Preparing DEM: processing {total_triangles:,} triangles for raster export.")
        for index, triangle in enumerate(model.triangles, start=1):
            if cancel.is_set():
                raise ImportCancelled()
            polygon = Polygon([xy(v) for v in triangle])
            polygons.append(polygon)
            bounds.append(_pixel_bounds(polygon.bounds, plan))
            if index % 100_000 == 0 or index == total_triangles:
                progress(f"Preparing DEM: {index:,}/{total_triangles:,} triangles processed.")
        evaluations = sum(
            max(0, right - left) * max(0, bottom - top) for left, top, right, bottom in bounds
        )
        if evaluations > request.limits.max_sample_evaluations:
            raise ResourceLimitError(
                f"Estimated triangle sample evaluations: {evaluations:,}; workload limit exceeded.",
                plan,
                "Increase cell size or reduce the terrain/DEM extent; long thin triangles "
                "may also need a better-shaped, breakline-preserving mesh.",
            )
        progress("Preparing DEM: building triangle lookup index; this may take time.")
        if cancel.is_set():
            raise ImportCancelled()
        tree = STRtree(polygons)
        if cancel.is_set():
            raise ImportCancelled()
        clip = Polygon([xy(v) for v in model.boundary]) if model.boundary else None
        xmin, _, _, ymax = plan.extent
        size = plan.cell_size
        valid_count = 0
        tile = request.limits.tile_size
        temporary = request.output.with_name(f".{request.output.stem}.{uuid4().hex}.part.tif")
        total_tiles = ceil(plan.width / tile) * ceil(plan.height / tile)
        done = 0
        progress(f"Writing DEM: 0/{total_tiles} tiles. Preview loads after export completes.")
        try:
            with rasterio.Env(GDAL_CACHEMAX=16 * 1024**2):
                with rasterio.open(
                    temporary,
                    "w",
                    driver="GTiff",
                    width=plan.width,
                    height=plan.height,
                    count=1,
                    dtype="float32",
                    crs=model.crs_wkt,
                    transform=Affine(size, 0.0, xmin, 0.0, -size, ymax),
                    nodata=nodata,
                    tiled=True,
                    blockxsize=256,
                    blockysize=256,
                    compress="deflate",
                    predictor=3,
                    BIGTIFF="IF_SAFER",
                ) as dst:
                    dst.set_band_description(1, "Terrain elevation")
                    dst.set_band_unit(1, request.elevation_unit)
                    dst.update_tags(
                        AREA_OR_POINT="Area",
                        elevation_units=request.elevation_unit,
                        vertical_reference=model.vertical_reference,
                        surface_method=model.method,
                        interpolation="linear_in_triangle_at_cell_center",
                        input_vertices=str(plan.input_vertices),
                        triangle_count=str(len(model.triangles)),
                        requested_extent=str(plan.requested_extent),
                        sample_coverage=request.sample_coverage,
                        contour_spacing_m=str(request.contour_spacing),
                        max_contour_edge_m=str(request.max_contour_edge),
                        coverage="boundary_and_available_triangles_cell_centers",
                    )
                    for row in range(0, plan.height, tile):
                        for col in range(0, plan.width, tile):
                            if cancel.is_set():
                                raise ImportCancelled()
                            height, width = (
                                min(tile, plan.height - row),
                                min(tile, plan.width - col),
                            )
                            data = np.full((height, width), nodata, dtype=np.float32)
                            occupied = np.zeros((height, width), dtype=np.bool_)
                            footprint = box(
                                xmin + col * size,
                                ymax - (row + height) * size,
                                xmin + (col + width) * size,
                                ymax - row * size,
                            )
                            for raw_i in tree.query(footprint):
                                if cancel.is_set():
                                    raise ImportCancelled()
                                i = int(raw_i)
                                left, top, right, bottom = bounds[i]
                                left, top = max(left, col), max(top, row)
                                right, bottom = min(right, col + width), min(bottom, row + height)
                                if left >= right or top >= bottom:
                                    continue
                                x, y = np.meshgrid(
                                    xmin + (np.arange(left, right, dtype=np.float64) + 0.5) * size,
                                    ymax - (np.arange(top, bottom, dtype=np.float64) + 0.5) * size,
                                )
                                a, b, c = model.triangles[i]
                                bx, by, cx, cy = b.x - a.x, b.y - a.y, c.x - a.x, c.y - a.y
                                det = bx * cy - by * cx
                                u = ((x - a.x) * cy - (y - a.y) * cx) / det
                                v = (bx * (y - a.y) - by * (x - a.x)) / det
                                inside = (u >= -1e-12) & (v >= -1e-12) & (u + v <= 1 + 1e-12)
                                if clip is not None:
                                    inside &= intersects_xy(clip, x, y)
                                values = (a.z + u * (b.z - a.z) + v * (c.z - a.z)) * scale
                                patch = data[top - row : bottom - row, left - col : right - col]
                                mask = occupied[top - row : bottom - row, left - col : right - col]
                                # Shared edge cells may be sampled twice; mesh validation has
                                # already checked that adjacent planes agree at their junction.
                                fill = inside & ~mask
                                patch[fill] = values[fill].astype(np.float32)
                                mask[fill] = True
                            valid_count += int(np.count_nonzero(occupied))
                            dst.write(data, 1, window=Window(col, row, width, height))
                            done += 1
                            if done == total_tiles or done % max(1, total_tiles // 100) == 0:
                                progress(f"Writing DEM: {done}/{total_tiles} tiles.")
            if not valid_count:
                raise ValueError(
                    "No cell centres intersect the terrain. Check extent and cell size."
                )
            if cancel.is_set():
                raise ImportCancelled()
            # Windows rename refuses an existing destination, including one created
            # after preflight. On POSIX use link to retain that no-overwrite guarantee.
            if os.name == "nt":
                os.rename(temporary, request.output)
            else:
                os.link(temporary, request.output)
        finally:
            temporary.unlink(missing_ok=True)
        return valid_count
