"""Rasterio affine/mask audit. Never clamp, swap, snap or reproject outlets."""

import math
from threading import Event

import rasterio
from pyproj import CRS
from rasterio.transform import rowcol
from rasterio.windows import Window

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.coordinates import (
    CoordinateReport,
    CoordinateRequest,
    DemCoordinates,
    OutletCoordinateCheck,
    PointLocation,
)


class RasterCoordinateInspector:
    def inspect(self, request: CoordinateRequest, cancel: Event) -> CoordinateReport:
        with rasterio.Env(GDAL_CACHEMAX=16 * 1024 * 1024), rasterio.open(request.dem) as dem:
            transform = dem.transform
            coefficients = tuple(float(v) for v in transform[:6])
            a, b, c, d, e, f = coefficients
            determinant = a * e - b * d
            affine_valid = (
                all(math.isfinite(v) for v in coefficients)
                and math.isfinite(determinant)
                and determinant != 0
            )
            diagnostics: list[str] = []
            if not affine_valid:
                diagnostics.append("Affine coefficients must be finite and invertible.")
            gcps, _ = dem.gcps
            if gcps or dem.rpcs:
                affine_valid = False
                diagnostics.append("GCP/RPC georeferencing requires an explicit affine DEM first.")
            if b == 0 and d == 0 and a > 0 and e < 0:
                direction = "north-up: columns increase east; rows increase south (Y decreases)"
            else:
                direction = f"column step XY=({a}, {d}); row step XY=({b}, {e})"
                diagnostics.append(
                    "Not a conventional north-up grid. Mapping uses its actual affine; "
                    "review/regrid before hydrology. No axis flip is performed."
                )
            dem_crs = dem.crs.to_wkt() if dem.crs else "Missing DEM CRS"
            matches = False
            try:
                matches = dem.crs is not None and CRS.from_user_input(dem_crs).equals(
                    CRS.from_user_input(request.crossings.crs_wkt), ignore_axis_order=True
                )
            except (ValueError, RuntimeError):
                diagnostics.append("Candidate or DEM CRS is missing or invalid.")
            if not matches:
                diagnostics.append(
                    "Candidate working CRS does not match DEM CRS. Recreate candidates in "
                    "the DEM CRS or explicitly reproject the DEM; no coordinates were changed."
                )
            if request.crossings.highway_source is None or request.crossings.culvert_source is None:
                diagnostics.append(
                    "Original DXF CRS assumptions unavailable; regenerate crossings."
                )
            corners = [
                (a * col + b * row + c, d * col + e * row + f)
                for col, row in ((0, 0), (dem.width, 0), (0, dem.height), (dem.width, dem.height))
            ]
            metadata = DemCoordinates(
                request.dem,
                dem_crs,
                (
                    min(p[0] for p in corners),
                    min(p[1] for p in corners),
                    max(p[0] for p in corners),
                    max(p[1] for p in corners),
                ),
                dem.width,
                dem.height,
                (a, b, c, d, e, f),
                affine_valid,
                direction,
                dem.nodata,
                tuple(diagnostics),
            )
            results: list[OutletCoordinateCheck] = []
            for point in request.crossings.points:
                if cancel.is_set():
                    raise ImportCancelled()
                if (
                    not matches
                    or not affine_valid
                    or not all(math.isfinite(v) for v in (point.x, point.y))
                ):
                    results.append(
                        OutletCoordinateCheck(
                            point,
                            PointLocation.UNVALIDATED,
                            diagnostic="Requires matching CRS, valid affine and finite X/Y.",
                        )
                    )
                    continue
                # Affine input is (column, row); Rasterio index output is (row, column).
                raw_row, raw_column = rowcol(transform, point.x, point.y, op=float)
                row_f, column_f = float(raw_row), float(raw_column)
                if not all(math.isfinite(v) for v in (column_f, row_f)):
                    results.append(
                        OutletCoordinateCheck(
                            point,
                            PointLocation.UNVALIDATED,
                            diagnostic="Affine inverse produced nonfinite pixel coordinates.",
                        )
                    )
                    continue
                row, column = dem.index(point.x, point.y)
                row, column = int(row), int(column)
                # Independently solve the translated affine equations to check XY order.
                dx, dy = point.x - c, point.y - f
                independent_column = (e * dx - b * dy) / determinant
                independent_row = (-d * dx + a * dy) / determinant
                pixel_tolerance = (
                    max(
                        math.ulp(row_f),
                        math.ulp(column_f),
                        (abs(e) + abs(d) + abs(a) + abs(b))
                        * max(math.ulp(point.x), math.ulp(point.y), math.ulp(c), math.ulp(f))
                        / abs(determinant),
                        1e-12,
                    )
                    * 32
                )
                ordering = (
                    (row, column) == (math.floor(row_f), math.floor(column_f))
                    and abs(independent_column - column_f) <= pixel_tolerance
                    and abs(independent_row - row_f) <= pixel_tolerance
                )
                x_back = a * column_f + b * row_f + c
                y_back = d * column_f + e * row_f + f
                # Numerical verification only: this tolerance never changes pixel classification.
                tolerance = max(math.ulp(point.x), math.ulp(point.y), 1e-12) * 32
                roundtrip = (
                    abs(x_back - point.x) <= tolerance and abs(y_back - point.y) <= tolerance
                )
                sides = tuple(
                    side
                    for side, hit in (
                        ("column=0", column_f == 0),
                        ("column=width", column_f == dem.width),
                        ("row=0", row_f == 0),
                        ("row=height", row_f == dem.height),
                    )
                    if hit
                )
                outside = not (0 <= column_f <= dem.width and 0 <= row_f <= dem.height)
                addressable = 0 <= column < dem.width and 0 <= row < dem.height
                state = "no addressable cell"
                elevation = None
                if addressable and ordering and roundtrip:
                    cell = dem.read(1, window=Window(column, row, 1, 1), masked=True)
                    value = float(cell.data[0, 0])
                    if bool(cell.mask.any()) or not math.isfinite(value) or value == dem.nodata:
                        state = "NoData"
                    else:
                        state = "valid"
                        elevation = float(cell.data[0, 0])
                location = (
                    PointLocation.OUTSIDE
                    if outside
                    else PointLocation.EDGE
                    if sides
                    else PointLocation.NODATA
                    if state == "NoData"
                    else PointLocation.VALID
                )
                if not ordering or not roundtrip:
                    location = PointLocation.UNVALIDATED
                results.append(
                    OutletCoordinateCheck(
                        point,
                        location,
                        row,
                        column,
                        row_f,
                        column_f,
                        state,
                        elevation,
                        sides,
                        column_f.is_integer() or row_f.is_integer(),
                        ordering,
                        roundtrip,
                        "Mapping verification failed." if not ordering or not roundtrip else "",
                    )
                )
            return CoordinateReport(metadata, request.crossings, matches, tuple(results))
