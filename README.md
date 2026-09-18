# Highway Drainage

Windows / VS Code desktop application with terrain DXF input, validated terrain-model
construction, tiled GeoTIFF export, highway/culvert crossing extraction, outlet
validation/snapping, and pyflwdir catchment delineation.

## Launch

From the project directory:

```powershell
.\.venv\Scripts\python.exe -m highway_drainage
```

In VS Code, select the local `.venv` interpreter and run this command in the terminal.
Close the window to exit. If an import is active, closing requests cancellation and
waits asynchronously for the worker to finish. DXF file reading itself cannot be
interrupted; cancellation is checked between files and entities.

## Shared previews

For satellite imagery **inside the preview panel**, tick **Google satellite imagery**.
Enter a Google Maps JavaScript API key in the local setup field and click **Connect**.
Enable the Maps JavaScript API and billing in that key's Google Cloud project. Restrict
the key to the Maps JavaScript API and the website `http://127.0.0.1:8765/*`.
Google usage charges may apply; see [Google's API setup guide](https://developers.google.com/maps/documentation/javascript/get-api-key).
The fixed loopback port is used only to serve the in-memory map page; it does not expose
project files. Close another instance using that port if the map page cannot start.

The embedded map uses Google's official satellite map type and retains Google's
attribution. Terrain View shows the cached DEM overlay. Drainage View shows highways,
culverts, original crossings, snapped outlets and catchments; its DEM overlay follows
**Show raster background**. Zoom, pan, extents and reset work in the map, with a separate
camera for each logical view. Uncheck **Google satellite imagery** to return to the
ordinary offline preview. Existing engineering results are reused.

The key is kept for the session unless **Remember key on this computer** is selected;
that option stores it in the current user's Qt settings (not in the repository).
Alternatively set `HIGHWAY_DRAINAGE_GOOGLE_MAPS_KEY` in your local environment. Key,
billing and network errors are displayed in the map. No API key is bundled. Qt WebEngine
is supplied by the existing PySide6 dependency; no extra package installation is needed.

**Open Google Earth Pro (separate window)** opens the current preview layers in Google Earth
Pro application (install Pro if it is not already available). It creates a local KMZ
snapshot with folders for highways, culverts, geometric crossings, snapped outlets,
and available catchment boundaries. Coordinates are converted from the project CRS
to WGS84 longitude/latitude. Catchment holes and disconnected areas are preserved;
simplified preview boundaries remain labelled as such.

From Terrain View, the KMZ includes the DEM display image. From Drainage View, it
includes that image only when **Show raster background** is checked. The cached image
is reprojected for display; no full DEM is loaded and no hydrology is rerun. Google
Earth supplies its own imagery and ground elevation beneath these overlays.

The export runs in a worker with progress/cancellation. A Save dialog lets you choose
the KMZ filename and folder. Existing files are replaced only after a warning is
confirmed and the new export completes successfully. The saved path is shown in the status message. If
automatic launch is unavailable, open that file using **File > Open** in Google Earth
Pro. This is an external Google Earth preview, not an embedded satellite view. No API
key or new Python dependency is required. Implementation follows Google's
[KML reference](https://developers.google.com/kml/documentation/kmlreference).

Use the single **Terrain View / Drainage View** selector above the main preview.
The workflow forms remain in scrollable tabs beside it, and the splitter adjusts
space between forms and preview. Only one preview scene is visible at a time.

- **Terrain View** shows a georeferenced elevation image plus filename, CRS,
  raster dimensions, bounds, pixel steps, sampled elevation range, units and NoData.
  It fills when opening an existing DEM / GeoTIFF, after DEM export, coordinate
  validation or outlet preparation. Before
  a DEM is available, terrain import supplies source/feature counts and datum information.
- **Drainage View** shares one scene between the crossing and outlet forms: blue
  highway lines, orange culverts, red geometric crossings, green snapped centers,
  grey containing pixels, and purple catchment boundaries. **Show raster background**
  adds the loaded georeferenced raster underneath; it is off by default and affects
  Drainage View only. The choice persists while changing views, refreshing results,
  or loading another TIFF during the session. Hidden raster extents are excluded
  from zoom to extents. Table/map selection
  remains linked. Catchment holes and separate components are retained.

DEM display snapshots and mask outlines load in existing worker threads when a
result becomes available. The application retains them in memory. Switching the
selector only changes the `QStackedWidget` page: it performs no raster reads,
polygon extraction, snapping, DEM generation or hydrology. Each scene retains its
zoom/pan state. New drainage results select Drainage View automatically.

Both previews use the same navigation controls:

- Mouse wheel: zoom around the pointer, including fractional wheel/trackpad motion.
- Left-click and drag: pan, including when the whole dataset currently fits.
- **Zoom to extents**: frame all displayed layers with a small margin.
- **Reset view**: restore the active view's default north-up, full-extent camera.

Reset and extents affect only the active preview. Terrain and Drainage retain their
own scale and pan position while switching, without rereading files or recomputing
results. Loading new data fits its new extent. Zoom is bounded between 1/32 and
1024 times the fitted scale to avoid unusable extremes. Clicking a crossing still
selects its table row; dragging over a marker pans without selecting or recentering.
All navigation behavior lives in presentation code (`navigation_view.py`).

The DEM image is capped at 768 pixels on its longest side; its reported elevation
range is sampled, not a full-raster statistic. NoData is transparent. Catchments
appear automatically as purple outlines with translucent fill over the raster.
Masks above 2 million cells use a coarser display grid, read in strips; any contributing
cell keeps its display cell visible, so tiny catchments are retained. A shared
250,000-vertex budget further coarsens complex outlines instead of skipping later
outlets. Simplified boundaries are labelled: small holes and gaps may disappear and
edges may expand by a display cell. Exported masks and computed areas remain at full
resolution. Small masks retain exact boundaries where the vertex budget permits.
Preview errors are reported without
discarding successful engineering results. Changing input data clears affected
snapshots; changing snapping settings retains the original crossing geometry.

The display-data port is `application/preview.py`, implemented with Rasterio in
`infrastructure/preview.py`. Qt renders immutable snapshots from `domain/preview.py`.
The selector and two persistent views live in `presentation/preview_panel.py`.
The [Qt stacked-widget API](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QStackedWidget.html)
provides visibility switching; no new dependencies or desktop framework were added.

## Existing rasters and task progress

**Open existing DEM / GeoTIFF** is available above the tabs. Loading reads metadata
and a bounded preview in a worker; it never modifies the source TIFF. It requires a
single elevation band, a valid affine transform and a projected metre CRS. Reproject
geographic/foot-based rasters in GIS first; a missing CRS must be assigned correctly.
Hydrology additionally requires square north-up pixels. The raster's Z units remain
as supplied; DXF unit conversion does not alter imported raster elevations.

All worker tasks show an animated Qt progress indicator and elapsed time. Known
counts (such as DEM tiles or prepared triangles) show real progress. Unknown-duration
reads, triangulation and compiled flow operations remain indeterminate rather than
showing a fabricated percentage. A `tqdm` console companion is enabled when a terminal
is attached. Cancel requests are honored at the next supported checkpoint; native
library operations cannot always stop immediately.

## Terrain input workflow

1. Use **Open existing DEM / GeoTIFF** to load a local single-band elevation raster.
   Its projected metre CRS becomes the project CRS, displayed read-only above the tabs.
   You can proceed directly to crossings and outlets without constructing terrain.
   To create a DEM from scratch, import terrain first; its detected/declarable source
   CRS establishes the initial metre CRS until the generated raster is available.
2. Use **Add DXF files** to select one or more files.
3. Select the linework role. Source CRS is detected from supported DXF GEODATA or a
   same-name `.prj` file. When that metadata is absent, the drawing is assumed to
   use the loaded raster CRS, and this assumption is recorded in the findings.
   There are no DXF source-setting controls. Z units default to `auto`,
   following the drawing units; `m` and international `ft` override Z only.
4. Optionally add exact, case-sensitive layer role overrides. These apply to all
   selected files and override each file's default linework role.
5. Select **Import and validate terrain** and inspect the findings.

| Entity | Interpretation |
| --- | --- |
| Triangular 3DFACE | Preserve original ordered vertices and face connectivity; no retriangulation |
| Quadrilateral 3DFACE | Preserve all four vertices; flag for planarity/triangulation review; do not select a diagonal |
| 3D POLYLINE | Preserve ordered vertices and closure; assign breakline, boundary, unassigned or ignore |
| LINE | Preserve its endpoints; may represent a breakline; a single open LINE is not a boundary |
| Other entities | Report as unsupported, including 2D polylines, curves, meshes and INSERT blocks |

No linework is converted into a random point cloud. A boundary must already be
closed (DXF closed flag or an exactly repeated endpoint), have nonzero XY area and
be simple. The importer never joins separate LINE entities or closes open chains.
Layer roles affect linework only, not 3DFACE surface entities. Modelspace is imported;
paperspace is not a terrain source. Ignored/unsupported entities receive findings.

Coordinates are extracted in drawing WCS. Supported GEODATA placement is applied,
then coordinates are transformed into the raster's project CRS with explicit X/Y
axis order. Project CRS units must be metres. Projected source CRSs may use feet or
other linear units. Known DXF `$INSUNITS` values (including feet, US survey feet,
inches, millimetres and centimetres) are converted into the source CRS units before
reprojection. This avoids scaling a foot-based CRS twice. Auto Z is converted to
metres separately. Missing drawing units produce a warning and use the declared or
detected source CRS units; no unit system is inferred from coordinate magnitude.
With no source metadata, the raster CRS is used as the source assumption. This
cannot detect or correct a drawing in a different, unidentified CRS. Conflicting
metadata and unsupported GEODATA transformations produce actionable errors. Local engineering grids still need proper georeferencing.
Z units are normalized to metres. GUI imports record `Survey elevations as supplied` automatically; no vertical-reference
field needs filling in. No vertical datum transformation or inferred elevation offset
is performed.

Non-finite XYZ, invalid boundaries, degenerate faces and zero-length/vertical XY edges
are rejected. Zero and negative Z are allowed; all-zero features are flagged for
review because DXF may have defaulted absent elevation components to zero. Optional
minimum/maximum Z limits are supported by the use-case request (not yet exposed in
the form). Shared exact XY positions with different Z values are reported as errors,
without averaging. Conflicting features remain available for inspection, so a dataset
with `has_errors` must not be used by future surface processing.

Exact duplicate geometry is detected within and across files, including reversed
lines and reversed/rotated faces and closed rings. The first geometry is retained;
findings link each duplicate's provenance to the original. Conflicting duplicate
roles are errors. This is deliberately exact matching after CRS normalization:
near-duplicates, differently segmented lines, interior surface overlaps and numeric
reprojection differences are not merged. No tolerance-based coordinate welding is
performed, so triangle coordinates are not moved.

The import report retains all findings and domain geometry in memory; the UI displays the
first 500 findings. File, layer and entity handle identify each source feature.
Changing inputs invalidates the previous result. Unassigned linework and quadrilateral
faces require the modeling rules below. Input files are never modified.

## Terrain models and GeoTIFF export

After importing terrain, open the **DEM / GeoTIFF** tab. Set the cell size, optional
extent, NoData, elevation units and an output filename, then select **Build terrain
and export GeoTIFF**. The job runs in a worker thread; progress and resource diagnostics
appear in the report. Cancellation is cooperative between geometry operations/tiles.

The export uses the active raster CRS, or the initial imported terrain CRS when
creating the first raster. Loading a different raster clears stale terrain,
crossings, outlet selections and catchments before another computation.
Output elevations may be metres or international feet; the vertical datum is unchanged.

Two model paths are selected from the actual input:

- **Existing faces:** preserve every valid triangle and its vertex order. Planar quads
  are triangulated without new vertices, using a 1e-6 metre elevation agreement check.
  Nonplanar quads must be explicitly triangulated in CAD. Interior face overlaps and
  discontinuities along shared edges are rejected, even if raster cell centres would
  miss the defect. A declared breakline must already consist of mesh edges with matching
  endpoint elevations. Otherwise the user must embed it in the source mesh or split it
  at existing mesh vertices; the exporter never silently rebuilds the surface.
- **Linework reconstruction:** require one explicit closed outer boundary. Breakline and
  boundary segments must already be noded at common endpoints with consistent elevations.
  Polygonize the resulting closed regions and apply constrained polygon triangulation.
  Verify that every constraint segment is a resulting triangle edge, that the boundary
  is covered, and that no new XY vertices were introduced. Dangling/cut constraints,
  unnoded intersections, overlaps and out-of-boundary segments are rejected with guidance.

One boundary can also clip the raster from existing faces without changing the mesh.
Multiple boundaries and hole semantics remain unsupported. Unassigned or skipped
unsupported input entities must be resolved or explicitly excluded before modeling.
No convex-hull filling across missing survey coverage and no line densification are used.

The model is held in library-independent `TerrainModel` / `Point3D` records. GeoTIFF
sampling uses barycentric (linear-in-triangle) interpolation at cell centres. Areas
outside the triangles or clipping boundary remain NoData; there is no extrapolation.
Sampling uses small windows, never a full-extent XY meshgrid. The GeoTIFF contains one
Float32 elevation band, CRS, north-up affine transform, NoData, band units, vertical
reference and interpolation/model metadata. NoData must lie outside the elevation
range, or be NaN. Float32 precision applies to output elevations and the NoData value.

Extent order is `xmin, ymin, xmax, ymax` in working-CRS metres. Blank extent uses the
boundary bounds, or terrain bounds when no boundary exists. The grid anchors at
`xmin, ymax`; dimensions round upward to whole cells, so the right and bottom bounds
may expand by less than a cell. Both requested and actual extents are reported.
No grid resampling occurs during writing.

Exports are written to a temporary file in the destination directory and published
only when complete. Replacing an existing file requires a warning-dialog confirmation
(Cancel is the default). Without confirmation, files that appear after preflight are
also protected. Failed or cancelled jobs preserve the old TIFF and remove their temporary raster. An extent
with no valid cell centres fails instead of publishing an empty DEM.

### Library choices

| Option | Evaluation and decision |
| --- | --- |
| NumPy + supplied triangle connectivity | Selected for bounded barycentric sampling; no replacement triangulation is needed |
| Shapely 2.1+ / GEOS 3.10+ | Selected for validation, indexing, polygonization and constrained polygon triangulation; supports the explicitly bounded constraint cases above |
| Rasterio | Selected for windowed GeoTIFF writing, affine georeferencing and metadata |
| startinpy | Useful for incremental point-based 2.5D terrain/interpolation; not needed to preserve existing faces, and not selected as an arbitrary breakline constraint solver |
| scipy.spatial.Delaunay | Point-based triangulation does not accept the required constrained edges through this interface; not selected |
| mesh_to_geotiff | Offers mesh gridding and overlap choices, but adds an unnecessary backend here; direct tiled sampling gives explicit grid, overlap and resource policies |

Documentation used for these decisions:
[startinpy](https://github.com/hugoledoux/startinpy),
[SciPy Delaunay](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.Delaunay.html),
[mesh_to_geotiff](https://github.com/Caumaker/mesh_to_geotiff),
[Shapely constrained triangulation](https://shapely.readthedocs.io/en/stable/reference/shapely.constrained_delaunay_triangles.html),
[Rasterio windowed I/O](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html).

### Resource protection

Default terrain limits are 100,000,000 raster cells, 5,000,000 input vertex references,
10,000,000 triangles, 24 GiB estimated working memory, 10,000,000 geometry-pair checks
and 2,000,000,000 triangle sample evaluations. These are processing ceilings, not
guarantees that sufficient RAM is currently available. Raster tiles default to 256 by 256 cells;
the GDAL cache is capped at 16 MiB. Limits are configurable through `ResourceLimits`
in the application request; the GUI uses these defaults.

Preflight runs before surface construction and raster allocation. It reports cell/sample
count, resolution, requested/actual extent, source vertex references, estimated triangle
count, working memory and raw raster size. One raster sample is taken per cell; triangle
bounding-box sampling work is checked separately before writing. Counts of vertex
references are conservative and may include shared vertices more than once.

The memory estimate reserves 2048 bytes per input vertex reference and per estimated
triangle, 160 bytes per tile cell and 32 MiB for fixed/native buffers. It is a planning
estimate, not an OS-enforced process memory cap; native-library overhead and already
resident data vary. Allocation failures also produce the same diagnostic context.

Excessive cell counts recommend a coarser resolution or smaller extent. Excessive model
memory recommends clipping/splitting the source terrain or simplifying it while
preserving constraints: coarsening the DEM alone does not reduce the input model size.
The importer still reads each source DXF into memory; these protections apply to model
generation and raster export, not to arbitrary-sized DXF parsing.

## Highway / culvert crossings

Open the **Highway / culvert crossings** tab, select a highway DXF and a culvert DXF,
and first select **Open ready raster TIFF** at the top of that tab. Highway/culvert
inputs are enabled after the raster loads. Its CRS is displayed read-only. DXF CRS
metadata is used when available; otherwise the raster CRS is assumed. All working horizontal CRS units
must be metres. Source drawing units are converted before reprojection, as in
terrain input. Optional, case-insensitive layer filters use
semicolon-separated names. Select the intended alignment/centreline layers; the
software does not infer which road edge or culvert symbol is hydraulically relevant.

Select **Extract lines and find crossings**. This workflow is independent of terrain
generation and does not load a DEM, snap outlets, infer flow, or delineate catchments.

Supported modelspace entities:

- LINE and 3D POLYLINE, preserving their straight segments.
- Ordinary 2D POLYLINE and LWPOLYLINE, including closed paths and bulge arcs. OCS
  elevation/extrusion is converted to WCS before the planar geometry is extracted.
- ARC and CIRCLE, approximated with a configurable sagitta tolerance (default 0.05 m).
- Ordinary nested INSERT blocks, applying insertion, base point, rotation, scaling
  and reflection through the transform chain. Layer 0 inherits the enclosing insert
  layer. Each instance retains its insert-handle chain and original leaf entity handle.

Fitted polylines, mesh POLYLINE modes, SPLINE, ELLIPSE, MINSERT arrays and other
unsupported entities are reported rather than guessed or destructively exploded.
Missing block definitions are reported. Cyclic/deep block references stop extraction.
Paperspace is not used. No input CAD file is modified.

The domain geometry contains clean immutable XY sequences and CAD references, not
ezdxf or Shapely objects. Consecutive identical XY vertices are removed; non-finite
coordinates and lines collapsed in plan view are rejected. Self-intersecting lines
are retained with a finding. Duplicate geometry is flagged but retained so separate
culvert identities are not lost. Identical reversed sequences are recognized;
near-duplicate geometry and differently segmented copies are not welded.

Curves are approximated in source drawing WCS before CRS transformation; nested
block scale bounds tighten the local tolerance. Curved results are explicitly flagged.
Intersections are exact for the resulting piecewise-linear XY geometry, not analytic
curve intersections. Near-tangent curve contacts can depend on the chosen tolerance.
CRS reprojection transforms vertices; no further densification is performed to model
projection curvature along long segments. Use suitable local projected data for
engineering verification and reduce curve tolerance where needed.

Shapely tests indexed highway/culvert segment pairs. Point contacts and ordinary
crossings are retained. A point on two adjacent highway elements is one candidate
per culvert element with both highway references. Multiple distinct crossings on one
culvert are separate candidates; separate culvert entities at the same XY stay separate.
Coincident sections are overlap findings, and contacts on the endpoints of those
sections are not fabricated into outlets. No near-miss snapping is performed.

Each candidate records its ID, easting/northing, source culvert reference, all matching
highway references, endpoint-contact flag and curve-approximation flag. The culvert
identifier is the full source file path plus insert-handle chain and entity handle;
it is not an inferred engineering asset ID from CAD text. Candidate IDs are numbered
within a result. All source information remains accessible in the domain result.

These are **plan-view candidates**. A crossing between features at different elevations
is still reported; it is not proof of a drainage connection or a verified culvert inlet.
Partial extraction is labelled when selected geometry is invalid or unsupported, so
zero candidates should not be interpreted as proof that no crossing exists.

The verification view displays highway lines in blue, culverts in orange and candidates
in red, with north upward. Wheel to zoom, drag to pan, or use **Fit view**. Select a table
row or click a point to highlight it in yellow and its source elements in purple. Hover
for identifiers; the coordinate table retains each crossing's source references. The
display translates large coordinates to a local drawing origin only for rendering;
stored coordinates remain in the working CRS. Editing inputs clears stale preview data.

The job runs outside the GUI thread and supports cooperative cancellation. Default
per-file limits are 100,000 visited CAD entities, 5,000 extracted elements, 100,000
vertices, 20,000 vertices per element and eight nested inserts. Intersection limits
are one million candidate segment pairs, 100,000 segment hits and 10,000 output points.
Excessive jobs recommend layer filtering, a coarser curve tolerance or splitting files.
DXF parsing itself still loads one document at a time and cannot be interrupted midway.
No additional runtime dependency was needed; visualization uses PySide6 graphics.

## Environment

Use 64-bit CPython 3.13 and a project-local `.venv`. The Python version range is
deliberately limited to the setup baseline; broaden it after testing other versions.
The optional startinpy backend remains uninstalled and untested.

Run these PowerShell commands from this directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The editable install makes imports resolve to `src/highway_drainage`. Python source
edits take effect without reinstalling. Reinstall after changing package metadata or
dependencies. `pyproject.toml` is the dependency source of truth; version ranges are
not a lock file. Setuptools is an isolated build dependency, not application runtime.

Activation is optional because the commands address the environment directly:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, keep using the explicit interpreter path. Changing
the machine's execution policy is unnecessary.

## Dependency groups

| Group | Packages | Setup stage |
| --- | --- | --- |
| Runtime | PySide6, ezdxf, pyproj, shapely, numpy, rasterio, pyflwdir, tqdm | Installed |
| Development (`dev`) | pytest, pytest-qt, ruff, mypy, types-shapely, types-tqdm | Installed |
| Candidate (`triangulation`) | startinpy | Not installed |

`mesh_to_geotiff` is unnecessary for the implemented export path. SciPy is now
installed as a pyflwdir dependency, along with Numba and llvmlite; terrain generation
does not call SciPy directly. The tested hydrology version is pyflwdir 0.5.12.
PySide6 installs its own required Qt bindings/components. Other tools also bring
their required transitive dependencies. NumPy and Rasterio are now direct runtime
dependencies; startinpy remains uninstalled.

## VS Code

Open this directory itself as the VS Code workspace. Install the recommended Python,
Pylance and Ruff extensions when prompted. These editor extensions are separate from
Python packages and are not installed by pip.

1. Press Ctrl+Shift+P.
2. Run **Python: Select Interpreter**.
3. Select `.venv\Scripts\python.exe`, or use **Enter interpreter path** to browse to it.
4. Open a new terminal and use the commands below.

The checked-in settings provide a default interpreter path and pytest discovery.
An existing interpreter selection can override that default; select it explicitly
when needed. No `PYTHONPATH` or `extraPaths` workaround is necessary.

## Checks

### Creating a GeoTIFF from general XYZ polylines

For 3D polylines whose elevations vary along the line, select **terrain samples**
in Terrain Input. This explicitly authorizes using their vertices as terrain
samples. All original XYZ values are retained; there is no constant-elevation
requirement or flattening. Identical XY with conflicting Z is still an error.

In DEM export choose **Sampled terrain coverage ? Use sample convex hull if no
boundary** to work without an outline. The surface fills the envelope of the
outermost samples, which can bridge unsurveyed gaps. Alternatively supply a closed
`boundary`; a supplied boundary always clips the result, even with the hull option.
Outside the sample hull remains NoData. The default coverage requires a boundary.

Leave **Polyline sample spacing (m)** blank to use original vertices only, or set
it to add samples with linearly interpolated X/Y/Z along segments. Raster cell size
is independent. **Maximum sample triangle edge (m)** optionally removes triangles
that span excessive distances. General line segments are not enforced breaklines;
this is sampled-point interpolation. Existing face/linework stitching is not
implemented. Strict `contour` remains available only for constant-elevation lines.

### Creating a GeoTIFF from contours

1. Add the contour DXF in Terrain Input and select `contour` as its linework role.
   Each 3D polyline must have constant elevation (agreement tolerance 0.000001 m).
   Source vertices and their elevations are preserved; duplicates are reported.
2. Add one closed outer outline with role `boundary`. This can be in a separate
   DXF or assigned through a layer override. In sampled terrain mode its XY defines the
   coverage mask; its Z values do not supply terrain elevations. Use the correct
   CRS and Z units, then import.
3. In DEM export, choose cell size and output filename. Optional **Polyline sample
   spacing (m)** adds intermediate points along segments. Blank uses source
   vertices only. This setting is independent of raster resolution. Excessive
   sampling is rejected before allocation, with counts and corrective guidance.
4. Optional **Maximum sample triangle edge (m)** excludes triangles with any
   longer edge, leaving NoData gaps instead of bridging them. Blank disables this
   additional filter. Select this threshold based on the survey's spacing.
5. Export and review the Terrain View before hydrology. To proceed without an
   outline, explicitly select convex-hull coverage as described above.

The implementation adapts `terrain_catchment_gui`'s sampled-point approach using
Shapely's existing Delaunay capability and the existing tiled rasterio exporter;
no new package is needed. It is approximate, unconstrained interpolation: contour
segments are not guaranteed triangle edges. Sparse contours can produce flat
triangles and poorly resolved ridges, valleys, summits and depressions.

Valid output cell centres must lie inside both the coverage boundary and a retained
triangle. There is no extrapolation beyond the sample convex hull. The rectangular
export extent only chooses the output grid; it does not authorize extrapolation.
The outline clips the output; source contours outside it can still influence
interpolation inside. Boundary holes and multiple outlines are not yet supported.

Crossing/overlapping contours, nonconstant elevations, and insufficient spatial
or elevation variation are rejected. Faces or breaklines mixed with contours are
explicitly rejected: stitching contour-derived terrain into face gaps is not yet
implemented. Export the contour-only dataset separately. Existing face and
constrained-breakline workflows remain available without selecting `contour`.

The GeoTIFF records the surface method and contour settings. Source survey quality
and hydrologic suitability still require review; a finer cell size does not add
survey information.

### Running checks

Tests are separated into `tests/unit`, `tests/integration`, and `tests/gui`.
Run engineering tests without loading Qt or launching a window:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -p no:pytest-qt
```

Run GUI tests separately with offscreen rendering:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTEST_QT_API = 'pyside6'
.\.venv\Scripts\python.exe -m pytest tests/gui
```

See [the test coverage guide](tests/README.md) for the workflow coverage and fixtures.
To check the full project:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
```

The tests generate small temporary DXFs to exercise geometry preservation, role
assignment, duplicates, invalid elevations and boundaries, CRS normalization and
file errors. DEM tests check analytic elevations, constrained ridges, georeferencing,
units, clipping, seams, resource guards and failure/cancellation cleanup. Qt tests
cover background import/export and diagnostic display. Crossing tests generate LINE,
polyline, bulge, arc, circle and nested block examples and verify provenance, OCS/CRS
conversion, overlaps and limits. Qt tests verify map/table linking and stale-result
invalidation. For a
headless runner, set `QT_QPA_PLATFORM=offscreen` in the test process environment.

Mypy uses strict checking for project sources and tests. A narrow missing-stubs
exception is configured for pytest-qt, rather than suppressing missing imports globally.
Ruff handles linting, import ordering and formatting; mypy checks types.

## Architecture

### Outlet coordinate validation

After extracting crossings, open **Outlet coordinate validation**, select the DEM,
and click **Validate outlet coordinates**. A successful DEM export also fills this
path. The report includes every candidate's unchanged X/Y, source identifiers,
integer and fractional row/column, cell state, affine round-trip and ordering checks.
Shared metadata includes DEM CRS, candidate working CRS, original DXF CRS
declarations, DEM bounds, affine coefficients, axis direction, dimensions and NoData.
DXF declarations remain survey assumptions; matching CRS labels cannot prove a
survey was correctly georeferenced.

The validator uses Rasterio band 1 and reads individual cells with their masks.
NoData sentinels, masked cells and nonfinite elevations are invalid. It never snaps,
clamps, swaps axes or reprojects points. Missing/mismatched CRS or an invalid affine
produces **not validated**, with a diagnostic, rather than a misleading cell result.
Rotated/south-up grids use their actual affine footprint and are flagged for review
before hydrology. Bounds are the enclosing min/max X/Y rectangle.

Rasterio fractional `(row, column)` indices determine classification; each is floored
for cell access. An independent inverse and forward affine check verify ordering and
round-trip accuracy within numerical tolerance; this tolerance never adjusts indices.
The report distinguishes the exact outer perimeter from internal pixel boundaries:

| Position in a north-up DEM | Classification / cell access |
| --- | --- |
| Just inside any outer edge | Valid or NoData according to its cell |
| Exact left/top edge | On edge; first column/row can be read |
| Exact right/bottom edge | On edge; column=width / row=height has no cell |
| Internal pixel boundary | Floor selects the cell on its right/bottom |
| Outside footprint | Outside DEM bounds; no cell is read |

Floating-point pixel coordinates are reported without rounding them into an adjacent
cell. Points extremely close to boundaries should be reviewed at the precision of
the source data. Editing crossing inputs or the DEM path clears the displayed audit.
The report is a snapshot; rerun validation if an external program replaces the DEM.
No hydrology or new dependencies are introduced by this step.

`domain/coordinates.py` holds immutable audit records; `application/coordinates.py`
defines the use case and inspection contract; `infrastructure/coordinates.py` owns
Rasterio/PyProj operations. The PySide6 panel displays results from a worker thread.
Tests use synthetic, non-square-cell GeoTIFFs to verify all four edges, corners,
internal boundaries, masks, CRS failures, rotation and Y-axis direction.

Coordinate conventions follow the
[Rasterio transform API](https://rasterio.readthedocs.io/en/stable/api/rasterio.transform.html)
and [raster masks documentation](https://rasterio.readthedocs.io/en/stable/topics/masks.html).

### Selecting outlet cells

In **Outlet coordinate validation**, select a DEM, choose a snapping mode, enter
the maximum distance in metres, then click **Select pour points**. This reruns
coordinate validation against the current DEM. Snapping does not modify the DXF,
DEM, geometric crossing, or its original pixel record.

Each `OutletSelection` retains three distinct things:

- `original.point`: the geometric crossing and its highway/culvert identifiers.
- `original.row/column`: the DEM pixel containing that crossing, including its
  original outside/edge/NoData classification. It may have no addressable cell.
- `pour_point`: the selected cell index and **cell-center** X/Y, elevation,
  movement distance and optional accumulation value; `None` when rejected.

**Nearest valid DEM cell** minimizes Euclidean distance from the original crossing
to valid cell centers. It excludes masks, NoData and nonfinite elevation values.
Results are explicitly **provisional DEM-valid**: an elevation cell is not evidence
of stream connectivity. **Highest flow accumulation** instead requires a supplied
accumulation GeoTIFF and a positive threshold in that raster's declared units
(e.g. contributing cells or square metres). Among eligible cells it maximizes
accumulation, then minimizes distance; remaining ties use row then column order.
No fallback to nearest-cell mode occurs if the threshold cannot be met.

Accumulation must be derived from the intended hydrologic DEM and have exactly
the same CRS, transform and dimensions. The application checks alignment and
valid values, but cannot establish the provenance of an arbitrary supplied raster.
This step consumes accumulation; it does not calculate flow direction or accumulation.
Results in this mode are **accumulation-qualified**, not proof that the selected
stream passes through the culvert. A radial search can cross a drainage divide;
review the map and use a suitably small radius. The catchment stage below builds a
flow model and rechecks those centers without changing them.

The maximum distance applies directly to original XY -> selected center, including
any move out of a NoData cell. It is inclusive, circular and never enlarged. A zero
radius permits only an exactly coincident eligible center. Outside points can be
selected only when a center is genuinely within that same distance; they are not
clamped to the raster edge. Rejected points retain their original data and reason.
Different culverts landing on one cell remain separate and are flagged for review.

Snapping currently requires a north-up projected DEM in metres. The map preserves
red geometric markers, outlines containing pixels in grey, and shows green
pour-point rings and movement lines. Tooltips and the text report retain both
coordinate sets. Changes to snapping inputs clear stale results.

Searches use bounded windows (250,000 cells per outlet, 5,000,000 cumulative by
default), with a 64 MiB raster storage-block guard. Excessive searches fail with
cell counts, radius, resolution, extent and corrective action. No new dependency
is required: Rasterio and NumPy implement this local search. The
[Rasterio window documentation](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html)
explains why storage-block size also matters. Pyflwdir's
[flow-path snapping](https://deltares.github.io/pyflwdir/latest/_generated/pyflwdir.FlwdirRaster.snap.html)
is a different operation requiring a flow-direction model, so it is not substituted
for this radius-based selection.

Outlet domain records, use case and Rasterio adapter live in each layer's
`outlets.py`. A Qt worker runs the operation; the panel/view only display its results.

### Catchment delineation

After **Select pour points**, open **Catchments**, enter an output directory
and the minimum contributing-cell accumulation, then click **Delineate catchments**.
Every prepared outlet gets a result, including rejected outlets with a reason and
no catchment mask. Prepared nearest-cell candidates are qualified against the newly
generated flow grid. Accumulation-selected outlets also retain their original
threshold, converted from cells, m2, ha or km2; unknown units require correction.
There is no further snapping or coordinate movement in this stage.

The implemented pyflwdir adapter performs:

1. Metadata, resource and NoData checks before reading the full DEM.
2. `dem.fill_depressions(..., outlets="edge", max_depth=-1)` to fill depressions
   to spill elevation and produce D8 directions. The input DEM is never overwritten.
3. `from_array(..., ftype="d8")` to build the flow network and check for invalid loops.
4. `upstream_area(unit="cell")` to calculate contributing-cell accumulation.
5. Checks of each selected cell's current validity, center coordinates, original
   distance limit, elevation and accumulation threshold.
6. A separate `basins(idxs=[outlet_index])` call for each accepted outlet. This is
   intentional: downstream outlets must include upstream catchments, so nested
   results overlap. Shared-cell culverts keep separate identities and masks.
7. A consistency check that catchment cell count equals outlet accumulation.

pyflwdir directly supplies those numerical operations. Its `from_dem` convenience
function also conditions a DEM and derives directions, but using `fill_depressions`
explicitly gives us the conditioned surface for inspection. Its `snap` method
traces a flow path; it is not used to silently change already prepared outlets.
See the official [conditioning](https://deltares.github.io/pyflwdir/latest/_generated/pyflwdir.dem.fill_depressions.html),
[accumulation](https://deltares.github.io/pyflwdir/latest/_generated/pyflwdir.FlwdirRaster.upstream_area.html),
and [basins](https://deltares.github.io/pyflwdir/latest/_generated/pyflwdir.FlwdirRaster.basins.html) APIs.

Each completed run contains:

- `conditioned_dem.tif`, including the original elevation units where declared.
- `flow_direction_d8.tif` (pyflwdir D8 codes; 247=NoData, 0=pit/outlet).
- `accumulation_cells.tif`, aligned with the input DEM and usable for outlet snapping.
- One `catchment_00001.tif` etc. per accepted outlet: 1=catchment, 0=other valid
  DEM cell, 255=NoData. Area is contributing-cell count times pixel area, in m2.
- `manifest.json`, recording the engine, conditioning/threshold settings, CRS,
  transform, original geometric coordinates, selected centers, IDs, output paths,
  area, status and diagnostics for every outlet.

Outputs are staged in a temporary sibling directory and published only after the
whole run succeeds. Reusing an existing output folder requires confirmation in a warning
dialog. Only generated result files are replaced; previous masks listed in the old
manifest are removed when no longer produced, and unrelated files are retained.
Failed/cancelled calculations preserve previous outputs. Publication failures roll
back replaced files; if restoration is blocked, the error identifies a retained recovery
folder. Replacing a DEM invalidates prepared outlets even if its filename stays the same.
Cancellation is checked between compiled operations;
Numba kernels and their initial compilation cannot be interrupted midway.

Hydrology requires north-up square pixels and a projected metre CRS. The Catchments
tab offers a **Processing budget** selector. **Standard** allows 2 million cells,
512 MiB estimated memory, 100 million cell/outlet visits and 512 MiB estimated
uncompressed output. **Large DEM** allows 50 million cells, 12 GiB estimated
memory, 2 billion cell/outlet visits and 4 GiB estimated output. This allows the
32,051,907-cell / 20-outlet case through preflight without changing the DEM or
outlet coordinates. It does not guarantee successful completion on available RAM. Diagnostics include resolution, extent,
estimated counts and recommended corrections. These are estimates, not guarantees
of peak process memory, particularly during the first Numba compilation.

Depression filling is the only conditioning policy currently implemented. There
is no culvert burning or enforced breach through highway embankments. NoData holes
are excluded and their margins are possible exits; a catchment touching any data
boundary is flagged because its contributing area may extend beyond DEM coverage.
Conditioned cell count and maximum fill are reported. Review those changes against
survey data: a mathematically valid D8 catchment does not prove a physical connection
through a particular culvert. Square pixels avoid assuming the upstream algorithm
accounts for anisotropic spacing. Elevation units remain those of the source DEM.

`domain/hydrology.py` holds records; `application/hydrology.py` defines the engine
contract/use case; `infrastructure/hydrology.py` is the only application module
importing pyflwdir. Qt uses a worker to invoke the use case and report per-outlet
results. Tests exercise synthetic drainage chains, nested/shared outlets, a closed
depression, changed DEMs, thresholds, NoData, resource limits and cancellation.

### Source packages

```text
src/highway_drainage/
    __main__.py     Module launcher
    main.py         Entry point and future composition root
    domain/         Models and rules
    application/    Use cases and contracts
    infrastructure/ Geospatial libraries and file access
    presentation/   PySide6 views
```

Presentation and infrastructure depend on application contracts and domain models.
Application code does not import concrete infrastructure or Qt. `main.py` wires the
DXF reader and terrain normalizer into the `ImportTerrain` use case. Domain records
contain only Python data, not ezdxf or GIS objects. Numerical geometry and CRS work
remain in infrastructure; the window only builds requests and displays results.

Keep real survey files in ignored `data/` or outside this repository. Keep generated
artifacts in ignored `output/` or `results/`. Small intentional test datasets belong
in `tests/fixtures/` and can be tracked in Git.
