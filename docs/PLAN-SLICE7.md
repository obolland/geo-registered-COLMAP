# Plan: slice 7, rider path, distance profile and route package

Status: proposed 2026-11-16. Inputs: `docs/OUTPUT-DESIGN.md` v0.2 sections 2-5 and 7, the refined session 1 reconstruction (`~/fieldtest2/colmap/s1-gsample`), its `images.json`, and the calibrated rig `rigs/handlebar-4cam-ft2-calibrated.toml`. Out of scope: splats, overview tiles and env layers (slice 8), multi-session joins (field test 2 session 2 later), the QA HTML report, the app-side trainer filter.

## Probe that shaped this (session 1, 2026-11-16)

- The rig origin from each registered camera, via the calibrated rig, agrees to 1.2 cm p50 and 2.4 cm p95 across cameras at the same pulse, so the rig pose per pulse is well defined.
- The rig tilts 2.9 degrees from level at p50 and 7.4 at p95 (lean, steering, mount), so the drop to the road must follow gravity, not the rig's z axis.
- SfM points on the road directly below the rig are sparse: a 1 m disc has fewer than 5 points at 81 of 625 pulses; a 2 m disc has at least 72 at every pulse (median 416).
- The surface under the rig reference point is a median 1.15 m below it (p5 1.11, p95 1.21), against 1.18 m measured by hand.

## Steps (each test-first, then an independent review)

1. **Rig pose per pulse** (`georoute.path`): the rig's world pose from every registered camera at that pulse (calibrated rig), combined (median position, mean rotation). Refuse a pulse whose cameras disagree by more than 5 cm (Measure); a pulse with no registered camera is a gap, bridged by interpolation in time and flagged.
2. **Road contact trace:** from the rig reference point, along the local ellipsoid normal (geodetic up at that point, not the session frame's U axis, which tilts by about 8 mm per km), onto a robust plane fitted to the SfM points in a 2 m disc around it within ±0.3 m of the expected road height (Measure). Contact sigma from the fit. Fewer than 20 points (Measure): use the nominal rig height instead and flag `surface-nominal`. Report the fitted height above road against the rig config, as a check on that measurement.
3. **Smoothed rider path:** reparameterise the trace by distance (stops collapse), then smoothing splines in east and north against distance, and a separate vertical one. Horizontal smoothing is comfort-first: yaw acceleration at or below 30 deg/s^2 under a reachable speed profile capped by 3 m/s^2 lateral and 1.5 m/s^2 longitudinal acceleration (Measure). Minimise departure from the trace subject to that, locally (start from the 0.10 m p95 registration fit and reduce spline weights in a window around each comfort violation), with a hard 0.5 m lateral departure cap (Measure). Vertical keeps the 0.03 m p95 bound because it is fit noise, not rider motion. Real captures include pull-overs and junction movements; the path absorbs them rather than over-smoothing the whole route. Where comfort cannot be met within the cap, keep the best fit under the cap and flag the segment. Heading from the tangent, curvature from the spline; no pitch or roll. The path, profile and grade are computed over the whole build, then sliced.
4. **Distance profile** at 1 m: `s` (3D along the surface), `s_h`, lat/lon (datum recorded, ETRS89 today), `h_ortho` with its geoid named (OSGM15 in Great Britain), `sigma_h`, `grade` (rise over horizontal run, about 10 m window, Measure), `curvature`, `corridor_left_m`/`corridor_right_m` (single pass: 1.0 each, marked not yet measured until the lateral pass is analysed), `surface` (manual tag, default `asphalt`), `quality` flags (RTK float or none from `images.json`, time-recovered, bridged gap, nominal surface).
5. **Segments and package:** cut at a configurable length (default 1000 m; field test 2 uses about 300 m to exercise joins, per validation plan item 1). Each segment has its own ENU origin at its middle (lat, lon, h_ell, ECEF), `path.json` at 0.5 m (`s, e, n, u_road, heading_rad, curvature_1pm` in the segment frame), `profile.json`, local bounds, and neighbours by 3D proximity (100 m, Measure). `route.json` carries schema version, datum, geoid, distance, loop flag (start and end within 5 m), app defaults, QA summary and provenance (session, rig hash, pipeline commit, tool versions, every parameter). `splats` is an empty list until slice 8. The package is written to a staging folder and moved into place, `route.json` last.
6. **JSON Schemas** for `route.json`, `path.json` and `profile.json` in `schemas/`, semver 1.0.0 with the v0.2 compatibility policy; tests validate the built package against them.
7. **QA** (`qa/<build_id>/report.json`, written even when a gate fails), per segment with severity: path to road (p95 vertical over 3 cm or lateral max over 0.5 m; lateral p95 is a reported result), path comfort (yaw acceleration over 30 deg/s^2 on the reachable speed profile), RTK gaps (longest non-fixed run over 50 m), registration (from the slice 6 report). A failing segment is listed for recapture; the rest still publishes, marked.
8. **CLI:** `georoute route-build <workspace> --images images.json --rig rig.toml --out <dir> --route-id <id> [--segment-length 300]`.

## New dependencies (need your OK)

- `scipy` for smoothing splines and robust fitting.
- `pyproj` for geodetic conversions and the OSGM15 geoid. The grid file (`uk_os_OSGM15_GB.tif`, PROJ CDN) is downloaded once into a data folder outside git, and its hash is recorded in provenance.
- `jsonschema` as a dev dependency, for the schema tests.

## How it's checked

- Unit tests on synthetic data: a known straight and a known circular road with planted weave, lean and a step in height, so the path, heading, curvature, grade and the registration bound have exact answers. A loop fixture, a stop (zero speed) fixture and a gap fixture.
- Then a real run on field test 2 session 1: gates, the height-above-road check against 1.18 m, and plots of the trace against the smoothed path (plan view, profile, curvature) for you to inspect.

## Done when

- `route-build` produces a schema-valid package and a QA report for field test 2 session 1.
- p95 path-to-road offsets are reported per segment, and any gate failure names the segment and the reason.
- Grade and heights are continuous across segment boundaries (computed globally, checked by a test).

## Risks

- **Comfort versus registration in tight turns:** a filter that is smooth enough at 18 m/s cuts the corner of a junction turn by more than 0.1 m. The bound wins, and the comfort gate reports the turn. The app's bend speed policy is what makes such a turn rideable.
- **Kerbs and verges in the 2 m disc** could bias the plane; the height window and the robust fit should reject them, and the height check shows if they don't.
- **The road surface from SfM points** is replaced by a fit to the splats once slice 8 exists; the interface stays the same.
