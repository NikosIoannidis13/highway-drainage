"""Extract supported modelspace entities without discarding their topology."""

from collections.abc import Iterable

from ezdxf.entities.line import Line
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


def _point(value: Vec3) -> Point3D:
    return Point3D(float(value.x), float(value.y), float(value.z))


class DxfTerrainReader:
    def read(self, source: TerrainSource) -> Iterable[TerrainFeature | ImportIssue]:
        try:
            document = readfile(source.path)
        except (OSError, DXFError) as exc:
            raise ValueError(f"Cannot read {source.path.name}: {exc}") from exc
        file_ref = EntityReference(source.path, "", "", "FILE")
        # This first implementation accepts metre-based drawing XY only.
        units = int(document.header.get("$INSUNITS", 0))
        if units not in (0, 6):
            yield ImportIssue(
                "error",
                "drawing_units",
                "DXF drawing units must be metres or unspecified.",
                file_ref,
            )
            return
        if units == 0:
            yield ImportIssue(
                "warning",
                "unspecified_units",
                "DXF units are unspecified; using the user's metre-based CRS declaration.",
                file_ref,
            )
        for entity in document.modelspace():
            ref = EntityReference(
                source.path, str(entity.dxf.handle), str(entity.dxf.layer), entity.dxftype()
            )
            if isinstance(entity, Face3d):
                vertices = tuple(_point(entity[index]) for index in range(4))
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
                    tuple(_point(v) for v in entity.points()),
                    closed=bool(entity.is_closed),
                    role=role,
                )
            elif isinstance(entity, Line):
                yield TerrainFeature(
                    ref, (_point(entity.dxf.start), _point(entity.dxf.end)), role=role
                )
            else:
                yield ImportIssue(
                    "warning",
                    "unsupported",
                    f"{ref.entity_type} not imported; no implicit flattening or block expansion.",
                    ref,
                )
