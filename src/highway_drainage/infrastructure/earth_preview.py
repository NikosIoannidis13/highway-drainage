"""Local KMZ export of cached preview geometry, transformed to WGS84 longitude/latitude."""

import math
import os
import warnings
from pathlib import Path
from threading import Event
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import Affine
from rasterio.warp import reproject
from shapely import build_area
from shapely.geometry import MultiLineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient

from highway_drainage.application.earth_preview import EarthPreview
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import XY
from highway_drainage.domain.preview import RasterPreview


def _element(parent: Element, name: str, value: str) -> Element:
    node = SubElement(parent, name)
    node.text = value
    return node


class KmzPreviewWriter:
    def write(
        self, preview: EarthPreview, destination: Path, cancel: Event, *, overwrite: bool = False
    ) -> None:
        def check() -> None:
            if cancel.is_set():
                raise ImportCancelled()

        check()
        source = CRS.from_user_input(preview.crs)
        if preview.crossings is not None and not source.equals(
            CRS.from_user_input(preview.crossings.crs_wkt)
        ):
            raise ValueError("Crossings and project raster CRS differ; reload the project data.")
        if preview.outlets is not None and not source.equals(
            CRS.from_user_input(preview.outlets.validation.dem.crs)
        ):
            raise ValueError("Outlets and project CRS differ; prepare outlets again.")
        transformer = Transformer.from_crs(source, "EPSG:4326", always_xy=True)
        kml = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
        document = SubElement(kml, "Document")
        _element(document, "name", "Highway Drainage preview")
        _element(document, "description", "Display snapshot; engineering results are unchanged.")
        for identifier, color in (
            ("highway", "ffac6817"),
            ("culvert", "ff0677d9"),
            ("crossing", "ff2626dc"),
            ("outlet", "ff4aa316"),
            ("catchment", "ffed3a7c"),
        ):
            style_node = SubElement(document, "Style", id=identifier)
            icon = SubElement(style_node, "IconStyle")
            _element(icon, "color", color)
            line_style = SubElement(style_node, "LineStyle")
            _element(line_style, "color", color)
            _element(line_style, "width", "2")
            fill = SubElement(style_node, "PolyStyle")
            _element(fill, "color", "40" + color[2:])

        def folder(name: str) -> Element:
            node = SubElement(document, "Folder")
            _element(node, "name", name)
            return node

        def placemark(parent: Element, name: str, style: str, description: str = "") -> Element:
            check()
            node = SubElement(parent, "Placemark")
            _element(node, "name", name)
            if description:
                _element(node, "description", description)
            _element(node, "styleUrl", "#" + style)
            return node

        def coordinates(points: tuple[XY, ...]) -> str:
            check()
            x, y = zip(*points, strict=True)
            lon, lat = transformer.transform(x, y, errcheck=True)
            if not all(
                math.isfinite(a) and math.isfinite(b) and -180 <= a <= 180 and -90 <= b <= 90
                for a, b in zip(lon, lat, strict=True)
            ):
                raise ValueError("Project coordinates cannot be displayed in Google Earth.")
            return " ".join(f"{a:.10f},{b:.10f},0" for a, b in zip(lon, lat, strict=True))

        def geometry(parent: Element, kind: str, points: tuple[XY, ...]) -> None:
            node = SubElement(parent, kind)
            if kind == "LineString":
                _element(node, "tessellate", "1")
            _element(node, "altitudeMode", "clampToGround")
            _element(node, "coordinates", coordinates(points))

        if preview.crossings is not None:
            for name, style, lines in (
                ("Highway", "highway", preview.crossings.highways),
                ("Culverts", "culvert", preview.crossings.culverts),
            ):
                parent = folder(name)
                for line in lines:
                    if len(line.vertices) >= 2:
                        geometry(
                            placemark(parent, line.reference.label, style),
                            "LineString",
                            line.vertices,
                        )
            parent = folder("Geometric crossings")
            for point in preview.crossings.points:
                geometry(
                    placemark(parent, point.identifier, "crossing", point.culvert.label),
                    "Point",
                    ((point.x, point.y),),
                )
        if preview.outlets is not None:
            parent = folder("Snapped outlets")
            for selection in preview.outlets.outlets:
                if selection.pour_point is None:
                    continue
                p = selection.pour_point
                original = selection.original.point
                description = (
                    f"Original XY: {original.x}, {original.y}; selected XY: {p.x}, {p.y}; "
                    f"movement: {p.distance:g} m. {selection.diagnostic}"
                )
                geometry(
                    placemark(parent, original.identifier, "outlet", description),
                    "Point",
                    ((p.x, p.y),),
                )
        if preview.boundaries is not None:
            parent = folder("Catchments")
            for outline in preview.boundaries.outlines:
                check()
                # Build area reconstructs holes and disconnected islands from the
                # non-crossing rings produced by the raster boundary preview.
                boundary_rings = [ring for ring in outline.rings if len(ring) >= 4]
                if not boundary_rings:
                    continue
                shape = build_area(MultiLineString(boundary_rings))
                if not isinstance(shape, (Polygon, MultiPolygon)) or not shape.is_valid:
                    raise ValueError(
                        f"Cannot export catchment {outline.identifier}: invalid boundary."
                    )
                node = placemark(
                    parent, outline.identifier, "catchment", preview.boundaries.diagnostic
                )
                multi = SubElement(node, "MultiGeometry")
                for polygon in shape.geoms if isinstance(shape, MultiPolygon) else [shape]:
                    polygon = orient(polygon, sign=1.0)
                    target = SubElement(multi, "Polygon")
                    _element(target, "tessellate", "1")
                    _element(target, "altitudeMode", "clampToGround")
                    for tag, rings in (
                        ("outerBoundaryIs", [polygon.exterior]),
                        ("innerBoundaryIs", polygon.interiors),
                    ):
                        for ring in rings:
                            boundary = SubElement(target, tag)
                            linear = SubElement(boundary, "LinearRing")
                            _element(
                                linear,
                                "coordinates",
                                coordinates(tuple((x, y) for x, y in ring.coords)),
                            )
        png = None
        if preview.raster is not None:
            check()
            png, bounds = _raster_overlay(preview.raster, source, transformer)
            overlay = SubElement(document, "GroundOverlay")
            _element(overlay, "name", "DEM preview (display resolution)")
            _element(overlay, "color", "aaffffff")
            icon = SubElement(overlay, "Icon")
            _element(icon, "href", "terrain.png")
            box = SubElement(overlay, "LatLonBox")
            for key, value in (
                ("north", bounds[3]),
                ("south", bounds[1]),
                ("east", bounds[2]),
                ("west", bounds[0]),
            ):
                _element(box, key, str(value))
        if png is None and not document.findall(".//Placemark"):
            raise ValueError("Load a raster or compute crossings before opening Google Earth.")
        check()
        temporary = destination.with_name(f".{destination.stem}.{uuid4().hex}.part.kmz")
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("doc.kml", tostring(kml, encoding="utf-8", xml_declaration=True))
                if png is not None:
                    archive.writestr("terrain.png", png)
            check()
            if overwrite:
                temporary.replace(destination)
            elif os.name == "nt":
                temporary.rename(destination)
            else:
                os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def _raster_overlay(
    snapshot: RasterPreview, crs: CRS, transformer: Transformer
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Warp only the bounded, cached RGBA image, never the full DEM."""
    transform = Affine(*snapshot.affine)
    # Sample every edge to encompass projection curvature and rotated grids.
    edge = np.linspace(0, 1, 33)
    cols = np.concatenate((edge, edge, np.zeros(33), np.ones(33))) * snapshot.width
    rows = np.concatenate((np.zeros(33), np.ones(33), edge, edge)) * snapshot.height
    lon, lat = transformer.transform(
        transform.a * cols + transform.b * rows + transform.c,
        transform.d * cols + transform.e * rows + transform.f,
        errcheck=True,
    )
    bounds = float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))
    if not all(math.isfinite(v) for v in bounds) or bounds[2] - bounds[0] > 180:
        raise ValueError("Raster overlay crosses the antimeridian or has invalid coordinates.")
    width, height = snapshot.width, snapshot.height
    data = np.frombuffer(snapshot.rgba, dtype="uint8").reshape(height, width, 4)
    output = np.zeros((4, height, width), dtype="uint8")
    target = Affine(
        (bounds[2] - bounds[0]) / width,
        0,
        bounds[0],
        0,
        -(bounds[3] - bounds[1]) / height,
        bounds[3],
    )
    reproject(
        source=np.moveaxis(data, 2, 0),
        destination=output,
        src_transform=transform,
        src_crs=crs,
        dst_transform=target,
        dst_crs="EPSG:4326",
        src_alpha=4,
        dst_alpha=4,
        resampling=Resampling.nearest,
        warp_mem_limit=16,
    )
    # PNG has no embedded georeferencing: its placement is stored in the KML.
    with warnings.catch_warnings(), MemoryFile() as memory:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with memory.open(driver="PNG", width=width, height=height, count=4, dtype="uint8") as dst:
            dst.write(output)
        return memory.read(), bounds
