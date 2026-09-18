# Test suites

Run commands from the project root with the local virtual environment.

| Suite | Purpose | Command |
| --- | --- | --- |
| Unit | In-memory application contracts, request validation, cancellation, resource planning | `.\.venv\Scripts\python.exe -m pytest tests/unit -p no:pytest-qt` |
| Integration | Real adapters using generated DXFs and GeoTIFFs, including the complete engineering workflow | `.\.venv\Scripts\python.exe -m pytest tests/integration -p no:pytest-qt` |
| GUI | PySide6 widgets, background workers, navigation, and cached view switching | `.\.venv\Scripts\python.exe -m pytest tests/gui` |

For headless GUI tests, set `$env:QT_QPA_PLATFORM = 'offscreen'` and
`$env:PYTEST_QT_API = 'pyside6'` before running pytest. Engineering tests require
neither a QApplication nor a display. An isolated interpreter test also prevents
Qt imports anywhere in the domain, application, or infrastructure packages.

Directory-based markers (`unit`, `integration`, `gui`) are assigned in
`conftest.py`. Use directory arguments to avoid collecting GUI modules: selecting
only `-m 'not gui'` still imports GUI test modules during collection.

## Coverage

| Workflow step | Integration coverage |
| --- | --- |
| DXF parsing, 3DFACE, 3D polylines, multi-file merging | `test_terrain_input.py`: roles, vertex order, provenance, duplicates, elevations, CRS, malformed inputs |
| Terrain models and raster generation | `test_dem.py`: preserved triangles, constrained breaklines, analytic elevations, extent, cell size, units, NoData, resource limits |
| Coordinate conversion and outlet validation | `test_coordinates.py`: four edges, exact boundaries, row/column order, affine transforms, CRS, NoData |
| Highway/culvert intersections | `test_crossings.py`: supported CAD curves/lines, intersections, identifiers, overlaps, CRS |
| Outlet snapping | `test_outlets.py`: distance limits, valid cells, accumulation thresholds, original versus snapped coordinates, rejection |
| Catchment delineation | `test_hydrology.py`: synthetic drainage, depression filling, nested/shared outlets, cell counts, masks, cancellation |
| Complete workflow | `test_complete_workflow.py`: two DXFs in both input orders, duplicate triangle, breakline, DEM, crossings, validation, snapping, per-outlet masks/areas, rejected outlet, accumulation-based preparation |

The complete workflow uses a planar terrain with analytically known elevations
and a five-cell drainage chain with known contributing areas. This checks stage
compatibility and output values, rather than just successful execution.

GUI coverage lives in `gui/`: `test_previews.py` checks that switching views
preserves cached results, scenes, and separate zoom transforms while engineering
calls and raster reads are forbidden. `test_preview_navigation.py` covers wheel
zoom, drag pan, extents, reset, and independent navigation state. Other GUI files
cover controls, workers, diagnostics, and invalidation of stale results.

Shared data builders live in `support/` and have no Qt dependencies. Test modules
do not import one another. DXFs, rasters, and catchment outputs are generated in
pytest temporary directories; no survey data or network access is required.
