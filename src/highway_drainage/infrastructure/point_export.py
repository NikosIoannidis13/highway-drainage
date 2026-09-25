"""Write a complete point Shapefile, with staged replacement and rollback."""

from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from threading import Event

import shapefile
from pyproj import CRS

from highway_drainage.application.point_export import (
    SHAPEFILE_SUFFIXES,
    DirectLineExport,
    LineExport,
    PointExport,
)
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import CrossingLimits
from highway_drainage.infrastructure.cad_lines import CadLineReader
from highway_drainage.infrastructure.cad_reference import resolve_source_crs


class ShapefilePointWriter:
    def _write_lines(self, stage: Path, request: LineExport, cancel: Event) -> None:
        with shapefile.Writer(
            str(stage), shapeType=shapefile.POLYLINE, encoding="utf-8", strict=True
        ) as writer:
            writer.field("line_id", "N", size=10)
            for name in ("kind", "source", "handle", "layer", "inserts"):
                writer.field(name, "C", size=254)
            writer.field("approx", "L")
            for number, line in enumerate(request.lines, start=1):
                if cancel.is_set():
                    raise ImportCancelled()
                if len(line.vertices) < 2 or any(
                    not isfinite(value) for vertex in line.vertices for value in vertex
                ):
                    raise ValueError("Line geometry needs at least two finite XY vertices.")
                ref = line.reference
                values = (request.kind, ref.path.name, ref.handle, ref.layer, "/".join(ref.inserts))
                if any(len(value.encode("utf-8")) > 254 for value in values):
                    raise ValueError("Line attributes exceed the Shapefile text field limit.")
                writer.line([list(line.vertices)])
                writer.record(number, *values, line.approximated)

    def write(
        self, request: PointExport | LineExport | DirectLineExport, cancel: Event
    ) -> PointExport | LineExport:
        if cancel.is_set():
            raise ImportCancelled()
        if isinstance(request, DirectLineExport):
            if request.source.path.suffix.lower() != ".dxf":
                raise ValueError("Select a DXF file for line export.")
            if not isfinite(request.tolerance) or request.tolerance <= 0:
                raise ValueError("Curve tolerance must be finite and greater than zero.")
            target_crs = (
                request.crs
                or resolve_source_crs(
                    request.source.path, request.source.crs, request.source.fallback_crs
                ).to_wkt()
            )
            extracted = CadLineReader().read(
                request.source, target_crs, request.tolerance, CrossingLimits(), cancel
            )
            issues = tuple(
                i for i in extracted.issues if i.severity == "error" or i.code == "unsupported"
            )
            request = LineExport(
                request.path,
                extracted.crs_wkt,
                extracted.lines,
                request.kind,
                request.overwrite,
                skipped=len(issues),
            )
        path = request.path.resolve()
        if path.suffix.lower() != ".shp":
            raise ValueError("Choose a .shp filename.")
        if not request.count:
            raise ValueError("There are no features to export.")
        crs = CRS.from_user_input(request.crs)
        targets = [path.with_suffix(s) for s in SHAPEFILE_SUFFIXES]
        if any(p.is_symlink() or (p.exists() and not p.is_file()) for p in targets):
            raise ValueError("Shapefile output conflicts with a directory or link.")
        if any(p.exists() for p in targets) and not request.overwrite:
            raise ValueError(
                "Shapefile components already exist. Choose a new name or confirm overwrite."
            )
        with TemporaryDirectory(prefix=".point-export-", dir=path.parent) as temporary:
            folder = Path(temporary)
            stage = folder / path.name
            if isinstance(request, LineExport):
                self._write_lines(stage, request, cancel)
            else:
                with shapefile.Writer(
                    str(stage), shapeType=shapefile.POINT, encoding="utf-8", strict=True
                ) as writer:
                    for name in ("point_id", "kind", "status", "acc_units"):
                        writer.field(name, "C", size=254)
                    for name in ("x", "y", "orig_x", "orig_y", "elevation", "snap_m", "flow_acc"):
                        writer.field(name, "N", size=24, decimal=8)
                    writer.field("culv_role", "C", size=16)
                    writer.field("manual", "L")
                    for point in request.points:
                        if cancel.is_set():
                            raise ImportCancelled()
                        numbers = (
                            point.x,
                            point.y,
                            point.original_x,
                            point.original_y,
                            point.elevation,
                            point.distance,
                            point.accumulation,
                        )
                        for value in numbers:
                            if value is not None and (
                                not isfinite(value) or len(f"{value:.8f}") > 24
                            ):
                                raise ValueError(
                                    "Point value exceeds the Shapefile numeric field range."
                                )
                        writer.point(point.x, point.y)
                        writer.record(
                            point.identifier,
                            point.kind,
                            point.status,
                            point.accumulation_units,
                            *numbers,
                            point.culvert_role,
                            point.manual,
                        )
            stage.with_suffix(".prj").write_text(crs.to_wkt(version="WKT1_ESRI"), encoding="utf-8")
            stage.with_suffix(".cpg").write_text("UTF-8", encoding="ascii")
            if cancel.is_set():
                raise ImportCancelled()
            backup = Path(mkdtemp(prefix=".point-export-backup-", dir=path.parent))
            saved: list[Path] = []
            published: list[Path] = []
            try:
                for target in targets:
                    if target.exists():
                        if not request.overwrite:
                            raise ValueError(
                                "Shapefile components appeared during export; choose a new name."
                            )
                        target.rename(backup / target.name)
                        saved.append(target)
                for suffix in SHAPEFILE_SUFFIXES[:5]:
                    target = path.with_suffix(suffix)
                    stage.with_suffix(suffix).rename(target)
                    published.append(target)
            except Exception:
                try:
                    for target in published:
                        target.unlink()
                    for target in saved:
                        (backup / target.name).rename(target)
                    backup.rmdir()
                except OSError as exc:
                    raise OSError(
                        f"Could not restore all files; backups retained at {backup}"
                    ) from exc
                raise
            try:
                for target in saved:
                    (backup / target.name).unlink()
                backup.rmdir()
            except OSError:
                pass  # Completed exports remain valid; retain any locked backups.
        return request
