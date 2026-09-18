"""Bounded cell-center search; accumulation is supplied, never inferred from elevation."""

import math
from contextlib import ExitStack
from dataclasses import replace
from threading import Event

import numpy as np
import rasterio
from rasterio.windows import Window

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import CoordinateReport, PointLocation
from highway_drainage.domain.outlets import (
    OutletSelection,
    PourPoint,
    SnapMode,
    SnapRequest,
    SnapResult,
)
from highway_drainage.infrastructure.terrain import _projected_metres


class RasterOutletSnapper:
    def snap(self, request: SnapRequest, audit: CoordinateReport, cancel: Event) -> SnapResult:
        selections: list[OutletSelection] = []
        with ExitStack() as stack:
            stack.enter_context(rasterio.Env(GDAL_CACHEMAX=16 * 1024 * 1024))
            dem = stack.enter_context(rasterio.open(request.coordinates.dem))
            _projected_metres(audit.dem.crs)
            a, b, c, d, e, f = audit.dem.affine
            if not audit.dem.affine_valid or b != 0 or d != 0 or a <= 0 or e >= 0:
                raise ValueError(
                    "Snapping requires a north-up projected DEM in metres; regrid first."
                )
            if tuple(dem.transform[:6]) != audit.dem.affine or (
                dem.width,
                dem.height,
                dem.crs.to_wkt() if dem.crs else "",
            ) != (audit.dem.width, audit.dem.height, audit.dem.crs):
                raise ValueError("DEM metadata changed during validation; retry.")
            accumulation = None
            if request.mode == SnapMode.ACCUMULATION:
                accumulation = stack.enter_context(rasterio.open(request.accumulation))
                if (
                    accumulation.crs != dem.crs
                    or accumulation.transform != dem.transform
                    or accumulation.shape != dem.shape
                    or accumulation.gcps[0]
                    or accumulation.rpcs
                ):
                    raise ValueError(
                        "Accumulation must have the DEM's exact CRS, affine and dimensions. "
                        "Use accumulation derived from this DEM grid; no resampling is performed."
                    )
            for source in (dem, accumulation):
                if source is not None:
                    block_rows, block_cols = source.block_shapes[0]
                    if block_rows * block_cols * np.dtype(source.dtypes[0]).itemsize > 64 * 1024**2:
                        raise ValueError("Raster storage block exceeds 64 MiB; retile the GeoTIFF.")
            total = 0
            for original in audit.outlets:
                if cancel.is_set():
                    raise ImportCancelled()
                if original.location == PointLocation.UNVALIDATED:
                    selections.append(
                        OutletSelection(original, None, "rejected", original.diagnostic)
                    )
                    continue
                point = original.point
                radius = request.max_distance
                # Broad bounding window only. Exact circular distance is enforced below.
                column_f = (point.x - c) / a
                row_f = (point.y - f) / e
                spans = (radius / a, radius / -e)
                if not all(math.isfinite(v) for v in (column_f, row_f, *spans)):
                    raise ValueError(
                        "Search radius/grid ratio is excessive; reduce snapping distance."
                    )
                left = max(0, math.floor(column_f - spans[0]) - 1)
                right = min(dem.width, math.ceil(column_f + spans[0]) + 1)
                top = max(0, math.floor(row_f - spans[1]) - 1)
                bottom = min(dem.height, math.ceil(row_f + spans[1]) + 1)
                cells = max(0, right - left) * max(0, bottom - top)
                total += cells
                if cells > request.max_window_cells or total > request.max_total_cells:
                    raise ValueError(
                        f"Snapping search estimates {cells:,} cells for this outlet and "
                        f"{total:,} cumulative cells; radius={radius:g} m, "
                        f"resolution={a:g} x {-e:g} m, DEM extent={audit.dem.bounds}. "
                        "Reduce the snapping distance or process fewer outlets."
                    )
                chosen = None
                if cells:
                    window = Window(left, top, right - left, bottom - top)
                    values = dem.read(1, window=window, masked=True)
                    valid = ~np.ma.getmaskarray(values) & np.isfinite(values.data)
                    if dem.nodata is not None:
                        valid &= values.data != dem.nodata
                    rows, columns = np.indices(values.shape)
                    rows += top
                    columns += left
                    xs = c + (columns + 0.5) * a
                    ys = f + (rows + 0.5) * e
                    distances = np.hypot(xs - point.x, ys - point.y)
                    valid &= distances <= radius
                    flow = None
                    if accumulation is not None:
                        flow = accumulation.read(1, window=window, masked=True)
                        valid &= ~np.ma.getmaskarray(flow) & np.isfinite(flow.data)
                        valid &= flow.data >= request.minimum_accumulation
                        if accumulation.nodata is not None:
                            valid &= flow.data != accumulation.nodata
                    indices = np.flatnonzero(valid)
                    if indices.size:
                        # Last lexsort key is primary: flow descending, then distance,
                        # then stable row-major index. Do not merge culvert identities.
                        order = np.lexsort((indices, distances.flat[indices]))
                        if flow is not None:
                            order = np.lexsort(
                                (
                                    indices,
                                    distances.flat[indices],
                                    -flow.data.ravel()[indices].astype("float64"),
                                )
                            )
                        best = int(indices[order[0]])
                        chosen = PourPoint(
                            int(rows.flat[best]),
                            int(columns.flat[best]),
                            float(xs.flat[best]),
                            float(ys.flat[best]),
                            float(values.data.flat[best]),
                            float(distances.flat[best]),
                            float(flow.data.flat[best]) if flow is not None else None,
                        )
                if chosen is None:
                    status = "rejected"
                    reason = (
                        f"No eligible cell center within {radius:g} m of original XY. "
                        "Check DEM coverage/masks, radius and accumulation threshold."
                    )
                elif accumulation is None:
                    status = "provisional DEM-valid"
                    reason = "Nearest valid elevation cell; drainage connectivity is not verified."
                else:
                    status = "accumulation-qualified"
                    reason = (
                        "Highest qualifying accumulation within radius; review against culvert. "
                        "Radial snapping does not establish flow-path connectivity."
                    )
                selections.append(OutletSelection(original, chosen, status, reason))
            owners: dict[tuple[int, int], list[str]] = {}
            for selection in selections:
                if selection.pour_point is not None:
                    p = selection.pour_point
                    owners.setdefault((p.row, p.column), []).append(
                        selection.original.point.identifier
                    )
            selections = [
                replace(
                    s,
                    shares_cell_with=tuple(
                        identifier
                        for identifier in owners[(s.pour_point.row, s.pour_point.column)]
                        if identifier != s.original.point.identifier
                    ),
                )
                if s.pour_point is not None
                else s
                for s in selections
            ]
            if cancel.is_set():
                raise ImportCancelled()
            return SnapResult(request, audit, tuple(selections))
