"""Plan and orchestrate bounded terrain-model construction and DEM export."""

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from decimal import ROUND_CEILING, Decimal
from math import hypot, isfinite, isnan, sqrt
from threading import Event
from typing import Protocol

from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import (
    DemRequest,
    DemResult,
    GridPlan,
    ResourceLimitError,
    TerrainModel,
)
from highway_drainage.domain.terrain import LineRole


def plan_dem(request: DemRequest) -> GridPlan:
    """Count first. No sample grid, GIS index or triangle arrays are allocated here."""
    if request.dataset.has_errors:
        raise ValueError("Resolve terrain import errors before generating a DEM.")
    if request.surface_mode not in ("single", "faces_with_polyline_gaps"):
        raise ValueError("Choose single terrain or 3D faces with polyline gap filling.")
    if request.surface_mode == "single" and any(
        i.code == "surface_z_difference" for i in request.dataset.issues
    ):
        raise ValueError(
            "Faces and sampled polylines differ in elevation at shared XY coordinates. "
            "Choose '3D faces with polyline gap filling' to preserve face elevations, "
            "or resolve those differences for a single surface."
        )
    if not request.dataset.features:
        raise ValueError("There is no terrain geometry to model.")
    unsupported = Counter(
        (i.reference.path.name, i.reference.layer, i.reference.entity_type)
        for i in request.dataset.issues if i.code == "unsupported"
    )
    if unsupported:
        details = "; ".join(
            f"{name}, layer '{layer}': {count} {entity_type}"
            for (name, layer, entity_type), count in unsupported.most_common(20)
        )
        if len(unsupported) > 20:
            details += f"; {len(unsupported) - 20} more groups (see import findings)"
        raise ValueError(
            f"DEM export blocked by skipped geometry: {details}. "
            "If these objects are not terrain, add their exact layer names under Terrain input "
            " > Layer overrides, set them to ignore, and reimport. "
            "If they contain terrain, convert them to supported geometry in CAD first. "
            "Do not ignore a layer that also contains needed contours."
        )
    if any(not f.is_face and f.role == LineRole.UNASSIGNED for f in request.dataset.features):
        raise ValueError(
            "Assign linework as terrain samples, contour, breakline, boundary or ignore "
            "before modeling."
        )
    if not isfinite(request.cell_size) or request.cell_size <= 0:
        raise ValueError("Raster cell size must be finite and greater than zero.")
    if request.elevation_unit not in ("m", "ft"):
        raise ValueError("Output elevation units must be m or international ft.")
    if not isnan(request.nodata) and (
        not isfinite(request.nodata) or abs(request.nodata) > 3.4028234663852886e38
    ):
        raise ValueError("NoData must be NaN or a finite Float32 value.")
    for name, value in (
        ("Contour spacing", request.contour_spacing),
        ("Maximum contour edge", request.max_contour_edge),
    ):
        if value is not None and (not isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be finite and greater than zero, or blank.")
    if request.sample_coverage not in ("boundary", "convex_hull"):
        raise ValueError("Sample coverage must be boundary or convex_hull.")
    limits = request.limits
    if (
        min(
            limits.max_cells,
            limits.max_input_vertices,
            limits.max_triangles,
            limits.max_memory_bytes,
            limits.max_geometry_checks,
            limits.max_sample_evaluations,
            limits.tile_size,
        )
        <= 0
    ):
        raise ValueError("Resource limits must be positive.")
    if limits.tile_size > 1024:
        raise ValueError("Tile size must not exceed 1024 cells per side.")
    nvertices = sum(len(f.vertices) for f in request.dataset.features)
    sample_vertices = sum(
        len(f.vertices) for f in request.dataset.features
        if not f.is_face and f.role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
    )
    # Count densification before allocating samples; resolution does not control it.
    if request.contour_spacing is not None:
        for feature in request.dataset.features:
            if feature.is_face or feature.role not in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES):
                continue
            pairs = list(zip(feature.vertices, feature.vertices[1:], strict=False))
            if feature.closed:
                pairs.append((feature.vertices[-1], feature.vertices[0]))
            for a, b in pairs:
                length = hypot(b.x - a.x, b.y - a.y)
                if not isfinite(length):
                    raise ValueError("Contour segment length exceeds numeric range.")
                intervals = int(
                    (
                        Decimal(str(length)) / Decimal(str(request.contour_spacing))
                    ).to_integral_value(rounding=ROUND_CEILING)
                )
                extra_samples = max(0, intervals - 1)
                nvertices += extra_samples
                sample_vertices += extra_samples
    faces = sum(max(0, len(f.vertices) - 2) for f in request.dataset.features if f.is_face)
    triangle_estimate = (
        # Face vertices already belong to preserved triangles. Only polyline samples
        # enter the fallback triangulation; boundary vertices only define clipping.
        faces + 2 * sample_vertices if request.surface_mode == "faces_with_polyline_gaps"
        else faces if faces else 2 * nvertices
    )
    boundary = next((f for f in request.dataset.features if f.role == LineRole.BOUNDARY), None)
    if request.extent is None:
        features = (boundary,) if boundary is not None else request.dataset.features
        xmin = ymin = float("inf")
        xmax = ymax = -float("inf")
        for feature in features:
            for v in feature.vertices:
                xmin, ymin = min(xmin, v.x), min(ymin, v.y)
                xmax, ymax = max(xmax, v.x), max(ymax, v.y)
        requested = (xmin, ymin, xmax, ymax)
    else:
        requested = request.extent
        xmin, ymin, xmax, ymax = requested
    if not all(isfinite(v) for v in requested) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Extent must contain finite xmin < xmax and ymin < ymax.")
    if not isfinite(xmax - xmin) or not isfinite(ymax - ymin):
        raise ValueError("Extent dimensions exceed the supported numeric range.")
    # Decimal division avoids float overflow for an accidentally microscopic cell size.
    width = int(
        (
            (Decimal(str(xmax)) - Decimal(str(xmin))) / Decimal(str(request.cell_size))
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    height = int(
        (
            (Decimal(str(ymax)) - Decimal(str(ymin))) / Decimal(str(request.cell_size))
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    # Keep estimates conservative: Python input/model records, GEOS indexes, temporary
    # topology, tile interpolation arrays, and a fixed 16 MiB GDAL cache allowance.
    memory = nvertices * 2048 + triangle_estimate * 2048 + limits.tile_size**2 * 160 + 32 * 1024**2
    plan = GridPlan(
        requested, requested, request.cell_size, width, height, nvertices, triangle_estimate, memory
    )
    if plan.cells > limits.max_cells:
        recommended = max(
            request.cell_size, sqrt(xmax - xmin) * sqrt((ymax - ymin) / limits.max_cells)
        )
        raise ResourceLimitError(
            f"Cell/sample limit exceeded ({limits.max_cells:,}).",
            plan,
            f"Increase cell size above approximately {recommended:g} m (allow for grid rounding), "
            "or reduce the extent. No raster samples have been allocated.",
        )
    actual = (xmin, ymax - height * request.cell_size, xmin + width * request.cell_size, ymax)
    plan = GridPlan(
        requested, actual, request.cell_size, width, height, nvertices, triangle_estimate, memory
    )
    if nvertices > limits.max_input_vertices or triangle_estimate > limits.max_triangles:
        raise ResourceLimitError(
            "Terrain vertex/triangle count limit exceeded.",
            plan,
            "Increase contour sampling spacing, disable intermediate sampling, "
            "or clip the input DXFs. "
            "A coarser raster alone does not reduce terrain-model memory.",
        )
    if memory > limits.max_memory_bytes:
        raise ResourceLimitError(
            f"Estimated working memory exceeds {limits.max_memory_bytes / 1024**2:g} MiB.",
            plan,
            "Increase polyline sample spacing or leave it blank for source vertices only. "
            "If it is already blank, clip/split the input terrain. A coarser raster or smaller "
            "export extent alone does not reduce the input model size.",
        )
    if xmin + request.cell_size == xmin or ymax - request.cell_size == ymax:
        raise ValueError("Cell size is below coordinate precision at this extent. Increase it.")
    return plan


class ModelBuilder(Protocol):
    def build(self, request: DemRequest, plan: GridPlan, cancel: Event) -> TerrainModel: ...


class RasterWriter(Protocol):
    def write(
        self,
        request: DemRequest,
        model: TerrainModel,
        plan: GridPlan,
        cancel: Event,
        progress: Callable[[str], None],
    ) -> int: ...


class GenerateDem:
    def __init__(self, builder: ModelBuilder, writer: RasterWriter) -> None:
        self._builder, self._writer = builder, writer

    def execute(
        self,
        request: DemRequest,
        cancel: Event | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> DemResult:
        token = cancel if cancel is not None else Event()
        report = progress if progress is not None else lambda _: None
        plan = plan_dem(request)
        if token.is_set():
            raise ImportCancelled()
        if request.output.suffix.lower() not in (".tif", ".tiff"):
            raise ValueError("Choose a .tif or .tiff output file.")
        if request.output.exists() and not request.overwrite:
            raise ValueError(
                "Output already exists. Choose a new filename; existing files are preserved."
            )
        if request.output.is_dir() or request.output.is_symlink():
            raise ValueError("Choose a regular GeoTIFF output file, not a directory or link.")
        if not request.output.parent.is_dir():
            raise ValueError("The output directory does not exist.")
        report(plan.describe())
        try:
            if request.surface_mode == "faces_with_polyline_gaps":
                model = self._build_combined(request, plan, token, report)
            else:
                model = self._builder.build(request, plan, token)
            if model.method in ("sampled_contours_unconstrained", "sampled_xyz_unconstrained"):
                report(
                    "Terrain approximation: XYZ samples are interpolated; source line segments "
                    "are not enforced edges. Coverage is limited to the sample hull "
                    "and any supplied boundary; "
                    "edge-filtered gaps remain NoData. Review before hydrology."
                )
            report(
                f"Surface built: {len(model.triangles):,} triangles. "
                "Preparing GeoTIFF export; the preview appears after export completes."
            )
            valid = self._writer.write(request, model, plan, token, report)
        except MemoryError as exc:
            raise ResourceLimitError(
                "Allocation failed despite the preflight estimate.",
                plan,
                "Clip/split the terrain and reduce the raster tile size. Close other memory-heavy "
                "applications; increasing cell size helps raster work but not input-model memory.",
            ) from exc
        return DemResult(request.output, plan, len(model.triangles), valid, model.method)

    def _build_combined(
        self, request: DemRequest, plan: GridPlan, cancel: Event,
        report: Callable[[str], None],
    ) -> TerrainModel:
        features = request.dataset.features
        faces = tuple(f for f in features if f.is_face)
        samples = tuple(
            f for f in features if not f.is_face
            and f.role in (LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES)
        )
        boundaries = tuple(f for f in features if not f.is_face and f.role == LineRole.BOUNDARY)
        if not faces or not samples:
            raise ValueError(
                "Gap filling needs both 3D faces and polylines assigned as terrain samples "
                "or contour. Import both DXFs in Terrain input first."
            )
        if any(not f.is_face and f.role not in (
            LineRole.CONTOUR, LineRole.TERRAIN_SAMPLES, LineRole.BOUNDARY,
        ) for f in features):
            raise ValueError(
                "For polyline gap filling, assign elevation linework as terrain samples "
                "or contour. Breaklines cannot be used as fallback samples automatically."
            )
        # Boundary Z is irrelevant: it clips both independently built surfaces in the writer.
        primary_request = replace(
            request, surface_mode="single", dataset=replace(request.dataset, features=faces),
        )
        fallback_request = replace(
            request, surface_mode="single",
            dataset=replace(request.dataset, features=samples + boundaries),
        )
        report("Building authoritative 3D-face surface…")
        primary = self._builder.build(primary_request, plan, cancel)
        if cancel.is_set():
            raise ImportCancelled()
        report("Building polyline gap-filling surface…")
        fallback = self._builder.build(fallback_request, plan, cancel)
        if len(primary.triangles) + len(fallback.triangles) > request.limits.max_triangles:
            raise ResourceLimitError(
                "Combined triangle limit exceeded.", plan, "Clip or split the input terrain."
            )
        report(
            "Rasterizing both surfaces on one grid: preserve face elevations and fill only "
            "uncovered cells with polyline interpolation. Sample segments are not enforced "
            "edges. Remaining gaps stay NoData; inspect the joins before hydrology."
        )
        return TerrainModel(
            primary.triangles + fallback.triangles, primary.crs_wkt,
            primary.vertical_reference, fallback.boundary, "faces_with_polyline_gaps",
            primary_triangle_count=len(primary.triangles),
        )
