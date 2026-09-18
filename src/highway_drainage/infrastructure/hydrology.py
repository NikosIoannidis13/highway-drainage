"""Only this adapter knows pyflwdir; independent full basins preserve nested outlets."""

import json
import math
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

import numpy as np
import numpy.typing as npt
import rasterio

from highway_drainage.application.hydrology import check_hydrology_budget
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.hydrology import CatchmentResult, HydrologyRequest, HydrologyResult
from highway_drainage.domain.outlets import SnapMode
from highway_drainage.infrastructure.result_files import publish_results
from highway_drainage.infrastructure.terrain import _projected_metres


def _check(cancel: Event) -> None:
    if cancel.is_set():
        raise ImportCancelled()


class PyFlwdirHydrology:
    def delineate(
        self, request: HydrologyRequest, cancel: Event, progress: Callable[[str], None]
    ) -> HydrologyResult:
        # Lazy import keeps Numba startup/compilation out of GUI startup.
        import pyflwdir
        from pyflwdir.dem import fill_depressions

        output = request.output.resolve()
        if output.exists() and not request.overwrite:
            raise ValueError(
                "Existing results are never overwritten without confirmation."
            )
        if output.exists() and not output.is_dir():
            raise ValueError("The catchment output path must be a directory.")
        prepared = request.prepared
        audit = prepared.validation
        with (
            rasterio.Env(GDAL_CACHEMAX=16 * 1024**2),
            rasterio.open(prepared.request.coordinates.dem) as source,
        ):
            if not source.crs:
                raise ValueError("DEM CRS is missing.")
            _projected_metres(source.crs.to_wkt())
            transform = source.transform
            if (
                not audit.crs_matches
                or not audit.dem.affine_valid
                or source.crs.to_wkt() != audit.dem.crs
                or tuple(transform[:6]) != audit.dem.affine
                or (source.width, source.height) != (audit.dem.width, audit.dem.height)
            ):
                raise ValueError(
                    "DEM grid/CRS no longer matches prepared outlets; prepare them again."
                )
            if (
                transform.b != 0
                or transform.d != 0
                or transform.a <= 0
                or transform.e >= 0
                or not math.isclose(transform.a, -transform.e, rel_tol=1e-12)
            ):
                raise ValueError(
                    "Hydrology requires north-up square cells in metres; regrid the DEM."
                )
            if source.gcps[0] or source.rpcs:
                raise ValueError(
                    "Hydrology requires affine georeferencing, not GCP/RPC coordinates."
                )
            cells = source.width * source.height
            count = sum(o.pour_point is not None for o in prepared.outlets)
            progress(check_hydrology_budget(request, cells, count, transform.a, audit.dem.bounds))
            _check(cancel)
            progress("Reading DEM; preserving NoData boundaries.")
            values = source.read(1, masked=True, out_dtype="float64")
            valid = ~np.ma.getmaskarray(values) & np.isfinite(values.data)
            if source.nodata is not None:
                valid &= values.data != source.nodata
            if not np.any(valid):
                raise ValueError("DEM has no valid elevation cells.")
            elevation = np.where(valid, values.data, np.nan)
            # The upstream implementation queues float32 elevations internally.
            if np.max(np.abs(elevation[valid])) > np.finfo(np.float32).max:
                raise ValueError("Elevation values exceed pyflwdir's finite float32 range.")
            crs = source.crs
            unit = source.units[0] or source.tags().get("elevation_unit", "unspecified DEM units")
            height, width = source.height, source.width
        minimum_cells = request.minimum_accumulation_cells
        if prepared.request.mode == SnapMode.ACCUMULATION:
            # Preserve the selection threshold when qualifying against this new model.
            factors = {
                "cell": 1.0,
                "cells": 1.0,
                "m2": transform.a * -transform.e,
                "m²": transform.a * -transform.e,
                "ha": transform.a * -transform.e / 1e4,
                "km2": transform.a * -transform.e / 1e6,
                "km²": transform.a * -transform.e / 1e6,
            }
            declared_unit = prepared.request.accumulation_units.strip().lower()
            if declared_unit not in factors:
                raise ValueError(
                    "Unknown accumulation units; prepare outlets using cells, m2, ha or km2."
                )
            minimum_cells = max(
                minimum_cells, prepared.request.minimum_accumulation / factors[declared_unit]
            )
        _check(cancel)
        progress("Conditioning depressions and deriving D8 flow; first run compiles Numba kernels.")
        conditioned, d8 = fill_depressions(elevation, nodata=np.nan, outlets="edge", max_depth=-1.0)
        _check(cancel)
        flow = pyflwdir.from_array(d8, ftype="d8", mask=valid, transform=transform, latlon=False)
        if not flow.isvalid:
            raise ValueError(
                "Generated flow network contains loops or invalid drainage directions."
            )
        progress("Calculating upstream contributing-cell accumulation.")
        accumulation = flow.upstream_area(unit="cell").astype("float64")
        accumulation[~valid] = np.nan
        _check(cancel)
        filled = conditioned[valid] - elevation[valid]
        filled_cells = int(np.count_nonzero(filled > 0))
        maximum_fill = float(np.max(filled))
        # Flag basins touching either raster edges or internal NoData holes.
        padded = np.pad(valid, 1, constant_values=False)
        boundary = np.zeros(valid.shape, dtype=bool)
        for dr in range(3):
            for dc in range(3):
                boundary |= valid & ~padded[dr : dr + height, dc : dc + width]
        del values, filled, padded
        diagnostics = (
            "Depressions filled to spill elevation; valid-data edges (including NoData holes) "
            "are potential exits. No culvert burning or breach enforcement was performed.",
            "Catchments are full independent upstream areas: nested catchments overlap. "
            "Area includes the outlet cell; shared-cell outlets retain separate results.",
            "Previously selected centers are checked against this flow model and remain fixed. "
            "A modeled catchment does not establish the physical culvert connection.",
            "Cancellation is checked between compiled operations, not inside Numba kernels.",
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        results: list[CatchmentResult] = []
        with TemporaryDirectory(prefix=".hydrology-", dir=output.parent) as temporary:
            stage = Path(temporary) / "result"
            stage.mkdir()

            def write(
                name: str, data: npt.NDArray[np.generic], nodata: float | int, units: str
            ) -> None:
                _check(cancel)
                with rasterio.open(
                    stage / name,
                    "w",
                    driver="GTiff",
                    width=width,
                    height=height,
                    count=1,
                    dtype=str(data.dtype),
                    crs=crs,
                    transform=transform,
                    nodata=nodata,
                    compress="deflate",
                    tiled=True,
                    blockxsize=256,
                    blockysize=256,
                ) as dst:
                    dst.write(data, 1)
                    dst.set_band_unit(1, units)
                    dst.update_tags(
                        engine=f"pyflwdir {pyflwdir.__version__}", source_dem=str(audit.dem.path)
                    )

            write("conditioned_dem.tif", conditioned, np.nan, unit)
            write("flow_direction_d8.tif", d8, 247, "D8")
            write("accumulation_cells.tif", accumulation, np.nan, "cells")
            for number, outlet in enumerate(prepared.outlets, start=1):
                _check(cancel)
                p = outlet.pour_point
                reason = ""
                acc = None
                if p is None:
                    reason = f"Outlet preparation rejected this point: {outlet.diagnostic}"
                elif outlet.status == "rejected" or outlet.original.cell_state == "not read":
                    reason = "Outlet coordinate preparation was not valid."
                elif not (0 <= p.row < height and 0 <= p.column < width):
                    reason = "Selected cell is outside the current DEM."
                else:
                    x = transform.c + (p.column + 0.5) * transform.a
                    y = transform.f + (p.row + 0.5) * transform.e
                    original = outlet.original.point
                    if (
                        not valid[p.row, p.column]
                        or not flow.mask.reshape(valid.shape)[p.row, p.column]
                    ):
                        reason = "Selected cell is not valid in the current flow model."
                    elif (p.x, p.y) != (x, y):
                        reason = "Selected XY does not match its DEM cell center; prepare again."
                    elif math.hypot(x - original.x, y - original.y) > prepared.request.max_distance:
                        reason = "Selected cell exceeds the original maximum snapping distance."
                    elif elevation[p.row, p.column] != p.elevation:
                        reason = "DEM elevation at outlet changed; prepare the outlet again."
                    else:
                        acc = float(accumulation[p.row, p.column])
                        if not math.isfinite(acc) or acc < minimum_cells:
                            reason = (
                                "Selected cell is below the current flow-accumulation threshold."
                            )
                if reason:
                    results.append(
                        CatchmentResult(outlet, "rejected", reason, accumulation_cells=acc)
                    )
                    continue
                assert p is not None
                progress(
                    f"Delineating outlet {number}/{len(prepared.outlets)}: "
                    f"{outlet.original.point.identifier}"
                )
                _check(cancel)
                labels = flow.basins(idxs=np.array([p.row * width + p.column], dtype=np.int64))
                basin = (labels > 0) & valid
                basin_cells = int(np.count_nonzero(basin))
                if not basin[p.row, p.column] or basin_cells != acc:
                    raise ValueError(
                        "Basin/accumulation consistency check failed; results not published."
                    )
                name = f"catchment_{number:05d}.tif"
                write(
                    name, np.where(~valid, 255, basin.astype("uint8")).astype("uint8"), 255, "mask"
                )
                touches = bool(np.any(basin & boundary))
                results.append(
                    CatchmentResult(
                        outlet,
                        "delineated",
                        "Review DEM coverage: catchment touches a data boundary."
                        if touches
                        else "Full contributing area in the generated D8 flow model.",
                        output / name,
                        basin_cells,
                        basin_cells * transform.a * -transform.e,
                        acc,
                        touches,
                    )
                )
            result = HydrologyResult(
                output,
                output / "conditioned_dem.tif",
                output / "flow_direction_d8.tif",
                output / "accumulation_cells.tif",
                tuple(results),
                filled_cells,
                maximum_fill,
                unit,
                diagnostics,
            )
            manifest = {
                "engine": f"pyflwdir {pyflwdir.__version__}",
                "conditioning": "fill_depressions: edge exits, max_depth=-1, D8",
                "minimum_accumulation_cells": request.minimum_accumulation_cells,
                "effective_minimum_accumulation_cells": minimum_cells,
                "snapping_mode": prepared.request.mode.value,
                "maximum_snapping_distance_m": prepared.request.max_distance,
                "crs_wkt": crs.to_wkt(),
                "affine": list(transform[:6]),
                "source_dem": str(audit.dem.path),
                "result": asdict(result),
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, default=str, indent=2), encoding="utf-8"
            )
            _check(cancel)
            publish_results(stage, output, request.overwrite, audit.dem.path)
        return result
