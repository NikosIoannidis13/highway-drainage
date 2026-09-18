"""GeoJSON and a bounded DEM image for the Google Maps JavaScript renderer."""

import base64
import json
import math
from threading import Event
from typing import Any

from pyproj import CRS, Transformer
from shapely import build_area
from shapely.geometry import MultiLineString, MultiPolygon, Polygon, mapping
from shapely.ops import transform as transform_geometry

from highway_drainage.application.satellite import SatelliteRequest
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.crossings import XY
from highway_drainage.infrastructure.earth_preview import _raster_overlay


class GoogleSatelliteBuilder:
    def build(self, request: SatelliteRequest, cancel: Event) -> str:
        preview = request.preview
        crs = CRS.from_user_input(preview.crs)
        transformer = Transformer.from_crs(crs, 4326, always_xy=True)
        features: list[dict[str, Any]] = []
        extent_points: list[XY] = []

        def check() -> None:
            if cancel.is_set():
                raise ImportCancelled()

        def point(x: float, y: float) -> XY:
            lon, lat = transformer.transform(x, y, errcheck=True)
            if not (
                math.isfinite(lon)
                and math.isfinite(lat)
                and -180 <= lon <= 180
                and -90 <= lat <= 90
            ):
                raise ValueError("Invalid geographic coordinates for satellite preview.")
            return float(lon), float(lat)

        def feature(kind: str, name: str, geometry: dict[str, Any]) -> None:
            check()
            features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": kind, "name": name},
                    "geometry": geometry,
                }
            )

        check()
        if preview.crossings is not None:
            if not crs.equals(CRS.from_user_input(preview.crossings.crs_wkt)):
                raise ValueError("Crossings and satellite project CRS differ.")
            for kind, lines in (
                ("highway", preview.crossings.highways),
                ("culvert", preview.crossings.culverts),
            ):
                for line in lines:
                    vertices = [point(x, y) for x, y in line.vertices]
                    if len(vertices) >= 2:
                        extent_points.extend(vertices)
                        feature(
                            kind,
                            line.reference.label,
                            {"type": "LineString", "coordinates": vertices},
                        )
            for p in preview.crossings.points:
                xy = point(p.x, p.y)
                extent_points.append(xy)
                feature("crossing", p.identifier, {"type": "Point", "coordinates": xy})
        if preview.outlets is not None:
            if not crs.equals(CRS.from_user_input(preview.outlets.validation.dem.crs)):
                raise ValueError("Outlets and satellite project CRS differ.")
            for selection in preview.outlets.outlets:
                if selection.pour_point is not None:
                    selected = selection.pour_point
                    xy = point(selected.x, selected.y)
                    extent_points.append(xy)
                    feature(
                        "outlet",
                        selection.original.point.identifier,
                        {"type": "Point", "coordinates": xy},
                    )
        if preview.boundaries is not None:
            for outline in preview.boundaries.outlines:
                check()
                rings = [ring for ring in outline.rings if len(ring) >= 4]
                if not rings:
                    continue
                area = build_area(MultiLineString(rings))
                if not isinstance(area, (Polygon, MultiPolygon)) or not area.is_valid:
                    raise ValueError(f"Invalid boundary for catchment {outline.identifier}.")
                for ring in rings:
                    extent_points.extend(point(x, y) for x, y in ring)
                feature(
                    "catchment",
                    outline.identifier,
                    dict(mapping(transform_geometry(transformer.transform, area))),
                )
        image = None
        if preview.raster is not None:
            check()
            png, image_bounds = _raster_overlay(preview.raster, crs, transformer)
            image = {
                "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                "bounds": image_bounds,
            }
            extent_points.extend(
                ((image_bounds[0], image_bounds[1]), (image_bounds[2], image_bounds[3]))
            )
        # Without an overlay, a loaded DEM still locates a raster-only project.
        if not extent_points and request.footprint is not None:
            raster = request.footprint
            a, b, c, d, e, f = raster.affine
            extent_points.extend(
                point(a * x + b * y + c, d * x + e * y + f)
                for x, y in (
                    (0, 0),
                    (raster.width, 0),
                    (raster.width, raster.height),
                    (0, raster.height),
                )
            )
        bounds: list[float] | None = None
        if extent_points:
            bounds = [
                min(p[0] for p in extent_points),
                min(p[1] for p in extent_points),
                max(p[0] for p in extent_points),
                max(p[1] for p in extent_points),
            ]
        check()
        return json.dumps(
            {
                "view": request.view,
                "geojson": {"type": "FeatureCollection", "features": features},
                "image": image,
                "bounds": bounds,
                "diagnostic": preview.boundaries.diagnostic if preview.boundaries else "",
            },
            allow_nan=False,
        )
