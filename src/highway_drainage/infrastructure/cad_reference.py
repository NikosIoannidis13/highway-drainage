"""Resolve CAD georeferencing and convert drawing units before CRS transformation."""

from dataclasses import dataclass
from math import isclose
from pathlib import Path

from ezdxf import units
from ezdxf.document import Drawing
from ezdxf.enums import InsertUnits
from ezdxf.filemanagement import readfile
from ezdxf.math import Matrix44, Vec3
from pyproj import CRS

from highway_drainage.domain.crossings import DrawingUnits


def _missing_crs(path: Path) -> ValueError:
    return ValueError(
        f"{path.name}: source CRS is missing. Load a project GeoTIFF first, "
        "or provide DXF georeferencing / a matching .prj file."
    )


def preflight_source_crs(path: Path, declared: str, fallback: str = "") -> None:
    """Reject missing CRS before constructing a potentially huge DXF document."""
    if declared.strip():
        source_crs(declared)
    if fallback.strip():
        source_crs(fallback)
    sidecar = path.with_suffix(".prj")
    if sidecar.is_file():
        source_crs(sidecar.read_text(encoding="utf-8-sig"))
        return
    if declared.strip() or fallback.strip():
        return
    # GEODATA is an ASCII type name in both text and binary DXF. A conservative
    # byte scan avoids parsing millions of entities just to discover its absence.
    # A match only defers to cad_frame's authoritative metadata validation.
    marker = b"GEODATA"
    try:
        with path.open("rb") as stream:
            tail = b""
            while chunk := stream.read(1024 * 1024):
                data = tail + chunk
                if marker in data:
                    return
                tail = data[-(len(marker) - 1):]
    except OSError as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc
    raise _missing_crs(path)


def source_crs(value: str) -> CRS:
    crs = CRS.from_user_input(value)
    if not crs.is_projected or len(crs.axis_info) != 2:
        raise ValueError("DXF source CRS must be a two-dimensional projected CRS.")
    if not isclose(
        crs.axis_info[0].unit_conversion_factor, crs.axis_info[1].unit_conversion_factor
    ):
        raise ValueError("DXF source CRS axes must use the same linear units.")
    return crs


@dataclass(frozen=True)
class CadFrame:
    crs: CRS
    xy_factor: float
    z_factor: float
    matrix: Matrix44 | None
    assumed: bool = False
    unit_summary: str = ""

    def point(self, value: Vec3) -> Vec3:
        if self.matrix is not None:
            converted = self.matrix.transform(value)
            return Vec3(converted.x, converted.y, value.z)
        return Vec3(value.x * self.xy_factor, value.y * self.xy_factor, value.z)


def cad_frame(
    document: Drawing, path: Path, declared: str, fallback: str = "",
    coordinate_units: DrawingUnits = DrawingUnits.HEADER,
) -> CadFrame:
    coordinate_units = DrawingUnits(coordinate_units)
    geo = document.modelspace().get_geodata()
    matrix = None
    detected = ""
    if geo is not None:
        try:
            matrix, epsg = geo.get_crs_transformation()
            detected = f"EPSG:{epsg}"
        except Exception as exc:
            raise ValueError(
                f"{path.name}: unsupported DXF georeferencing; export georeferenced "
                "WCS geometry from CAD before import."
            ) from exc
    sidecar = path.with_suffix(".prj")
    if sidecar.is_file():
        sidecar_crs = source_crs(sidecar.read_text(encoding="utf-8-sig"))
        if detected and not sidecar_crs.equals(source_crs(detected)):
            raise ValueError(f"{path.name}: embedded CRS conflicts with its .prj file.")
        detected = sidecar_crs.to_wkt()
    if not detected and not declared.strip() and not fallback:
        raise _missing_crs(path)
    crs = source_crs(detected or declared or fallback)
    if detected and declared.strip() and not crs.equals(source_crs(declared)):
        raise ValueError(f"{path.name}: declared CRS conflicts with DXF metadata.")
    if geo is not None and (
        not isclose(crs.axis_info[0].unit_conversion_factor, 1.0)
        or not isclose(geo.dxf.horizontal_unit_scale, geo.dxf.vertical_unit_scale)
    ):
        raise ValueError(
            f"{path.name}: unsupported GEODATA unit configuration; "
            "export georeferenced WCS geometry first."
        )
    unit_code = int(document.units)
    try:
        header_units = InsertUnits(unit_code).name
    except ValueError:
        header_units = f"unknown ({unit_code})"
    overrides = {
        DrawingUnits.METRES: 1.0, DrawingUnits.MILLIMETRES: 0.001,
        DrawingUnits.FEET: 0.3048, DrawingUnits.US_SURVEY_FEET: 1200 / 3937,
        DrawingUnits.INCHES: 0.0254,
    }
    if geo is not None and coordinate_units not in (DrawingUnits.HEADER, DrawingUnits.SOURCE_CRS):
        raise ValueError(
            f"{path.name}: embedded GEODATA defines coordinate placement and units. "
            "Use 'Coordinates already in source CRS' or 'Use DXF header units' "
            "to retain its georeferencing."
        )
    try:
        if coordinate_units == DrawingUnits.SOURCE_CRS:
            metres = float(crs.axis_info[0].unit_conversion_factor)
        elif coordinate_units in overrides:
            metres = overrides[coordinate_units]
        elif coordinate_units == DrawingUnits.HEADER:
            metres = (
                float(units.conversion_factor(InsertUnits(unit_code), InsertUnits.Meters))
                if unit_code else float(crs.axis_info[0].unit_conversion_factor)
            )
        else:
            raise ValueError(f"Unknown coordinate units: {coordinate_units}")
    except (ValueError, TypeError, IndexError) as exc:
        if unit_code in (21, 22, 23, 24):
            metres = (1200 / 3937) * {21: 1, 22: 1 / 12, 23: 3, 24: 5280}[unit_code]
        else:
            raise ValueError(f"{path.name}: unsupported drawing units ({unit_code}).") from exc
    # GEODATA's matrix already includes drawing-to-CRS XY scaling and placement.
    z_metres = float(geo.dxf.vertical_unit_scale) if geo is not None else metres
    xy_factor = metres / crs.axis_info[0].unit_conversion_factor
    operation = (
        "embedded GEODATA placement and units applied" if geo is not None else
        f"XY scale {xy_factor:.12g} before CRS transformation "
        f"({coordinate_units.value.replace('_', ' ')})"
    )
    return CadFrame(
        crs,
        xy_factor,
        z_metres,
        matrix,
        assumed=not detected and not declared.strip(),
        unit_summary=f"DXF header: {header_units}; {operation}.",
    )


def resolve_source_crs(path: Path, declared: str, fallback: str = "") -> CRS:
    preflight_source_crs(path, declared, fallback)
    if declared.strip():
        return source_crs(declared)
    sidecar = path.with_suffix(".prj")
    if sidecar.is_file():
        return source_crs(sidecar.read_text(encoding="utf-8-sig"))
    try:
        return cad_frame(readfile(path), path, declared, fallback).crs
    except OSError as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc
