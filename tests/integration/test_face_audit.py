"""Coverage, elevation, provenance and early failure guarantees for overlap cleanup."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from shapely import union_all
from shapely.geometry import Polygon

from highway_drainage.application.dem import plan_dem
from highway_drainage.application.face_report import write_face_report
from highway_drainage.application.terrain import ImportCancelled
from highway_drainage.domain.dem import DemRequest
from highway_drainage.domain.terrain import FaceAuditOptions, TerrainDataset
from highway_drainage.infrastructure.face_audit import FaceAuditor, plane_z
from highway_drainage.infrastructure.raster import GeoTiffWriter
from highway_drainage.infrastructure.surface import SurfaceBuilder
from tests.support.dem import dem_service, plane
from tests.support.face_audit import overlapping
from tests.support.terrain_input import document, load, save


def test_default_import_checks_all_pairs_and_blocks_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = overlapping(tmp_path, 0.05)
    audit = dataset.face_audit
    assert audit is not None and audit.complete and audit.blocked
    finding, = audit.findings
    assert finding.max_z_difference == pytest.approx(0.05)
    assert finding.area == pytest.approx(4.5)
    assert finding.first_fraction == pytest.approx(4.5 / 8)
    assert finding.second_fraction == pytest.approx(4.5 / 8)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Known conflicts must fail before model building or raster writing")

    monkeypatch.setattr(SurfaceBuilder, "build", unexpected)
    monkeypatch.setattr(GeoTiffWriter, "write", unexpected)
    request = DemRequest(dataset, tmp_path / "blocked.tif", surface_mode="faces_with_polyline_gaps")
    with pytest.raises(ValueError, match="DEM build blocked") as exc:
        dem_service().execute(request)
    for face in dataset.features:
        assert face.reference.handle in str(exc.value)
    assert not request.output.exists()


def test_accepted_partial_overlaps_preserve_union_and_face_planes(tmp_path: Path) -> None:
    dataset = overlapping(tmp_path, 0.001)
    dataset = FaceAuditor().audit(dataset, FaceAuditOptions(5, 0.002), Event(), lambda _: None)
    audit = dataset.face_audit
    assert audit is not None and not audit.blocked
    original = [Polygon([(v.x, v.y) for v in t]) for t in audit.triangles]
    cleaned = [Polygon([(v.x, v.y) for v in t]) for t in audit.cleaned_triangles]
    assert union_all(original).symmetric_difference(union_all(cleaned)).area < 1e-12
    assert sum(p.area for p in cleaned) == pytest.approx(union_all(original).area)
    for t in audit.cleaned_triangles:
        assert any(all(v.z == pytest.approx(plane_z(source, v.x, v.y)) for v in t)
                   for source in audit.triangles)
    result = dem_service().execute(DemRequest(dataset, tmp_path / "cleaned.tif", cell_size=0.5))
    assert result.method == "cleaned_faces"


def test_fully_covered_agreeing_face_is_removed_without_losing_coverage(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 1), (4, 0, 1), (0, 4, 1)])
    doc.modelspace().add_3dface([(1, 1, 1), (2, 1, 1), (1, 2, 1)])
    dataset = load(save(doc, tmp_path / "redundant.dxf"))
    audit = dataset.face_audit
    assert audit is not None and not audit.blocked
    assert audit.findings[0].second_fraction == 1
    assert audit.cleaned_triangles == (audit.triangles[0],)


def test_microscopic_overlap_with_large_z_conflict_is_not_ignored(tmp_path: Path) -> None:
    doc = document()
    doc.modelspace().add_3dface([(0, 0, 1), (4, 0, 1), (0, 4, 1)])
    doc.modelspace().add_3dface([(1, 1, 1.05), (1.00001, 1, 1.05), (1, 1.00001, 1.05)])
    audit = load(save(doc, tmp_path / "tiny.dxf")).face_audit
    assert audit is not None and audit.blocked
    assert audit.findings[0].area < 1e-9
    assert audit.findings[0].max_z_difference == pytest.approx(0.05)


def test_shared_xy_face_disagreements_use_audit_tolerance(tmp_path: Path) -> None:
    doc = document()
    points = [(0, 0, 1), (4, 0, 1), (0, 4, 1)]
    doc.modelspace().add_3dface(points)
    doc.modelspace().add_3dface([(x, y, z + 0.000001) for x, y, z in points])
    dataset = load(save(doc, tmp_path / "shared.dxf"))
    dataset = FaceAuditor().audit(dataset, FaceAuditOptions(0, 0.000002), Event(), lambda _: None)
    assert not dataset.has_errors
    assert dataset.face_audit is not None and not dataset.face_audit.blocked
    dem_service().execute(DemRequest(dataset, tmp_path / "shared.tif"))


def test_audit_reused_but_changed_faces_rechecked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = plane(tmp_path)

    def unexpected(*args: object, **kwargs: object) -> TerrainDataset:
        raise AssertionError("audit was called")

    monkeypatch.setattr(FaceAuditor, "audit", unexpected)
    request = DemRequest(dataset, tmp_path / "cached.tif")
    SurfaceBuilder().build(request, plan_dem(request), Event())
    changed = replace(dataset.features[0], vertices=tuple(
        replace(v, z=v.z + 1) for v in dataset.features[0].vertices
    ))
    request = replace(request, dataset=replace(dataset, features=(changed, dataset.features[1])))
    with pytest.raises(AssertionError, match="audit was called"):
        SurfaceBuilder().build(request, plan_dem(request), Event())


def test_incomplete_or_cancelled_audit_cannot_clear_build(tmp_path: Path) -> None:
    dataset = overlapping(tmp_path)
    limited = FaceAuditor(max_checks=0).audit(dataset, FaceAuditOptions(), Event(), lambda _: None)
    assert limited.face_audit is not None and not limited.face_audit.complete
    with pytest.raises(ValueError, match="Audit incomplete"):
        plan_dem(DemRequest(limited, tmp_path / "limited.tif"))
    cancel = Event()

    def stop(message: str) -> None:
        if message.startswith("Checking face overlaps"):
            cancel.set()

    with pytest.raises(ImportCancelled):
        FaceAuditor().audit(dataset, FaceAuditOptions(), cancel, stop)


def test_report_contains_sources_metrics_and_blocked_status(tmp_path: Path) -> None:
    dataset = overlapping(tmp_path, 0.05)
    audit = dataset.face_audit
    assert audit is not None
    path = tmp_path / "report.csv"
    write_face_report(audit, path)
    text = path.read_text(encoding="utf-8-sig")
    assert "build_blocked,True" in text
    assert "maximum_z_difference_m" in text
    for face in dataset.features:
        assert face.reference.handle in text


@pytest.mark.parametrize("options", [FaceAuditOptions(-1, 0), FaceAuditOptions(1, float("nan"))])
def test_invalid_tolerances_fail_before_geometry(tmp_path: Path, options: FaceAuditOptions) -> None:
    with pytest.raises(ValueError, match="tolerances"):
        FaceAuditor().audit(plane(tmp_path), options, Event(), lambda _: None)
