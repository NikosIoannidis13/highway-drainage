"""Extract supported modelspace entities without discarding their topology."""

from collections.abc import Iterable

from ezdxf.entities.line import Line
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.entities.polyline import Polyline
from ezdxf.entities.solid import Face3d
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFError
from ezdxf.math import Vec3

from highway_drainage.domain.terrain import (
    EntityReference,
    ImportIssue,
    LineRole,
    Point3D,
    TerrainFeature,
    TerrainSource,
)
from highway_drainage.infrastructure.cad_reference import cad_frame, preflight_source_crs


class DxfTerrainReader:
    def read(self, source: TerrainSource) -> Iterable[TerrainFeature | ImportIssue]:
        preflight_source_crs(source.path, source.crs, source.fallback_crs)
        try:
            document = readfile(source.path)
        except (OSError, DXFError) as exc:
            raise ValueError(f"Cannot read {source.path.name}: {exc}") from exc
        file_ref = EntityReference(source.path, "", "", "FILE")
        frame = cad_frame(document, source.path, source.crs, source.fallback_crs)
        if frame.assumed:
            yield ImportIssue(
                "warning",
                "assumed_raster_crs",
                "DXF has no CRS metadata; assumed the project raster CRS.",
                file_ref,
            )
        z_scale = frame.z_factor if source.z_unit == "auto" else 1.0

        def point(value: Vec3) -> Point3D:
            converted = frame.point(value)
            return Point3D(converted.x, converted.y, value.z * z_scale)

        if int(document.units) == 0:
            yield ImportIssue(
                "warning",
                "unspecified_units",
                "Drawing units unspecified: using source CRS units for XY and automatic Z.",
                file_ref,
            )
        elif int(document.units) != 6:
            yield ImportIssue(
                "info",
                "drawing_units",
                "Drawing units converted to source CRS units; "
                f"automatic Z factor={frame.z_factor:g} m.",
                file_ref,
            )
        for entity in document.modelspace():
            ref = EntityReference(
                source.path, str(entity.dxf.handle), str(entity.dxf.layer), entity.dxftype()
            )
            if isinstance(entity, Face3d):
                vertices = tuple(point(entity[index]) for index in range(4))
                # DXF stores a triangular face by repeating its third vertex.
                if vertices[2] == vertices[3]:
                    vertices = vertices[:3]
                yield TerrainFeature(ref, vertices, is_face=True, closed=True)
                continue
            role = source.role_for(ref.layer)
            if role == LineRole.IGNORE:
                yield ImportIssue("info", "ignored", "Excluded by linework role.", ref)
                continue
            if isinstance(entity, Polyline) and entity.is_3d_polyline:
                if int(entity.dxf.flags) & 6:
                    yield ImportIssue(
                        "warning", "unsupported", "Fitted polylines need explicit conversion.", ref
                    )
                    continue
                yield TerrainFeature(
                    ref,
                    tuple(point(v) for v in entity.points()),
                    closed=bool(entity.is_closed),
                    role=role,
                )
            elif isinstance(entity, LWPolyline) or (
                isinstance(entity, Polyline) and entity.is_2d_polyline
            ):
                if entity.has_arc or (
                    isinstance(entity, Polyline) and int(entity.dxf.flags) & 6
                ):
                    yield ImportIssue(
                        "warning", "unsupported",
                        "Curved/fitted 2D polylines need explicit conversion to supported "
                        "3D terrain geometry before import.", ref,
                    )
                    continue
                yield ImportIssue(
                    "info", "ignored",
                    f"Straight {ref.entity_type} disregarded for terrain modeling.", ref,
                )
            elif isinstance(entity, Line):
                yield TerrainFeature(
                    ref, (point(entity.dxf.start), point(entity.dxf.end)), role=role
                )
            else:
                yield ImportIssue(
                    "warning",
                    "unsupported",
                    f"{ref.entity_type} not imported; no implicit flattening or block expansion.",
                    ref,
                )
