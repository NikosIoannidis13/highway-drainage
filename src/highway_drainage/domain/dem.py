"""Terrain surfaces, raster requests and resource estimates without GIS dependencies."""

from dataclasses import dataclass
from pathlib import Path

from highway_drainage.domain.terrain import Point3D, TerrainDataset

type Extent = tuple[float, float, float, float]
type Triangle = tuple[Point3D, Point3D, Point3D]


@dataclass(frozen=True)
class ResourceLimits:
    max_cells: int = 100_000_000
    max_input_vertices: int = 5_000_000
    max_triangles: int = 10_000_000
    max_memory_bytes: int = 24 * 1024**3
    max_geometry_checks: int = 10_000_000
    max_sample_evaluations: int = 2_000_000_000
    tile_size: int = 256


@dataclass(frozen=True)
class DemRequest:
    dataset: TerrainDataset
    output: Path
    cell_size: float = 1.0
    extent: Extent | None = None
    nodata: float = -9999.0
    elevation_unit: str = "m"
    limits: ResourceLimits = ResourceLimits()
    contour_spacing: float | None = None
    max_contour_edge: float | None = None
    sample_coverage: str = "boundary"
    overwrite: bool = False
    surface_mode: str = "single"


@dataclass(frozen=True)
class GridPlan:
    requested_extent: Extent
    extent: Extent
    cell_size: float
    width: int
    height: int
    input_vertices: int
    estimated_triangles: int
    estimated_memory_bytes: int

    @property
    def cells(self) -> int:
        return self.width * self.height

    def describe(self) -> str:
        return (
            f"Resolution: {self.cell_size:g} m; extent (xmin, ymin, xmax, ymax): {self.extent}. "
            f"Grid: {self.width:,} x {self.height:,} = {self.cells:,} cells/samples. "
            f"Estimated input/sample references: {self.input_vertices:,}; "
            f"estimated triangles: {self.estimated_triangles:,}. "
            f"Estimated working memory: {self.estimated_memory_bytes / 1024**2:,.1f} MiB; "
            f"raw Float32 raster: {self.cells * 4:,} bytes."
        )


class ResourceLimitError(ValueError):
    """An actionable failure before allocating excessive processing resources."""

    def __init__(self, reason: str, plan: GridPlan, action: str) -> None:
        self.plan = plan
        super().__init__(f"{reason}\n{plan.describe()}\nRecommended action: {action}")


@dataclass(frozen=True)
class TerrainModel:
    triangles: tuple[Triangle, ...]
    crs_wkt: str
    vertical_reference: str
    boundary: tuple[Point3D, ...] | None
    method: str
    # In a priority model, these leading triangles are authoritative faces.
    primary_triangle_count: int | None = None


@dataclass(frozen=True)
class DemResult:
    output: Path
    plan: GridPlan
    triangle_count: int
    valid_cells: int
    method: str
