# Pipeline output design (proposal, v0.2)

Status: v0.2, 2026-11-12. Revised after two independent red teams (`research/design-challenge-merged.md`, which links both) and Olly's answers (`research/STATUS.md`). Not yet built. Labels: **Decided** (Olly's requirement or a verified constraint), **Proposed** (design choice), **Measure** (a starting value that an experiment must confirm before it is fixed).

Changes from v0.1: the rider path now comes from the reconstructed rig pose rather than the antenna track; path, profile and grade are computed per build and then sliced; tuning policy (trainer command filter, comfort speed) moves to the app; ownership and prefetch use 3D proximity; streaming first; multi-session joins, provenance, schema compatibility and staged QA are first-class; the MVP is restructured so it can fail.

## 1. What the outputs must serve

| Consumer | Needs |
|---|---|
| Renderer (PlayCanvas first, Spark as fallback) on WebXR, Quest 3 reference device, PCVR high tier | Tiled splats with LOD, small local coordinates, one global splat budget, **streaming** along a known route |
| Rider motion and comfort | A smooth, level, wobble-free path registered to the visible road; curvature |
| Trainer (FTMS) | Elevation and one canonical physics grade by distance; the app turns these into trainer commands |
| Steering riser | A measured corridor per segment (single pass: about ±1 m) |
| Map, minimap, distance markers, Strava export | Lat/lon per sample, route overview, distance along route |
| Pipeline QA and Olly | Numeric, per-segment gates that fail loudly and say what to recapture |

**Decided:** streaming is the primary delivery mode; whole-route download before riding stays an option, not the plan. MVP is forward gaze only (rear-facing cameras come after the MVP). One capture pass per route. 20-80 km routes are multi-session builds.

## 2. Coordinate frames

Unchanged in principle from v0.1, endorsed by both reviews (all 16 agents).

- **Earth anchor:** ECEF in float64. The input datum (today ETRS89 via Point One `LOCAL`) is recorded, never assumed WGS84.
- **Segment frame:** the route is cut into segments of about 1 km (**Measure**). Each has a local ENU frame with its origin near the segment middle, stored as lat, lon, ellipsoidal height and ECEF position. All splats, path samples and bounds in a segment are relative to that origin.
- **Joins:** the transform between segments comes exactly from the ECEF anchors. The app keeps a floating origin near the rider and **re-bases atomically** (camera and every loaded asset in the same frame).
- **Vertical:** grade and elevation come from each sample's own geodetic height, never from a frame's U axis. Displayed and exported elevation is orthometric, with the geoid named (OSGM15 in Great Britain, EGM2008 elsewhere; OSGM15 coverage outside GB to be checked).
- **Axes:** data stays ENU, right-handed, z-up; the conversion to the renderer's y-up happens in the runtime segment transform.

**Compute globally, then slice (new).** The rider path, profile and grade are computed over the whole build in one continuous pass, then cut into segments. Fitting them per segment would put a step in heading, curvature and grade at every boundary (a 0.001 1/m curvature step is 0.32 m/s^2 of lateral jerk at 18 m/s).

**3D proximity, not only arclength (new).** Routes fold back on themselves (hairpins, out-and-backs, loops). Splat ownership, segment bounds and prefetch use 3D proximity; segment bounds may overlap; the manifest carries a spatial neighbour index; a duplicate-splat gate catches double ownership.

## 3. Published route package

```
<route_id>/<build_id>/
  route.json                       manifest: the app's only entry point (published last, atomically)
  overview.json                    whole-route polyline at 10 m (lat, lon, h_ortho, s) for map and minimap
  segments/<seg_id>/
    profile.json                   1 m samples (section 5)
    path.json                      rider path in segment frame (section 4)
    splats/<format>/<tier>/        e.g. sog-streamed/quest, sog-streamed/pcvr
    env/<format>                   sky and far-field environment layer
qa/<build_id>/                     staged outside the package, written even when the build fails
  report.html, report.json
```

Kept for reprocessing, not published: per-block lossless masters (3DGS PLY, float32, full SH), COLMAP model, ingest outputs, run log. A Spark `.RAD` package can be generated from the same masters.

### `route.json` essentials (Proposed)

- `schema` with a semver version and a written **compatibility policy**: minor versions only add optional fields, so an app reading 1.x can read any 1.y; a major version bump is a breaking change. JSON Schemas live in this repo and the app validates on load.
- Route level: `datum`, `geoid`, `distance_m` (route-global `s`, 3D), `loop`, defaults for app-side policy (section 5), and the **QA summary** with its threshold-set version.
- Per segment: `seg_id`, `s_range` (route-global), `origin` (lat, lon, h_ell, ecef), `bounds_local`, `neighbours` (segment ids within a 3D radius, for prefetch on folded routes), `session_id`, `session_join` (whether the start boundary joins a different session), `corridor_left_m`/`corridor_right_m` max, and `quality`.
- **Engine-neutral splat descriptor:** `splats` is a list of `{format, tier, url, bytes, bytes_by_lod, splats_by_lod, sh_degree, antialiased}`. Renderer-specific details stay inside each format's own files. The app picks the first format and tier it supports.
- **Provenance** per build: sessions, rig config hash, pipeline commit, tool versions (COLMAP, gsplat, splat-transform) and the full parameter set (training, path smoothing, grade, QA thresholds).

### Streaming (Proposed)

The segment is the unit of loading and caching. The app keeps the current segment plus enough ahead to cover a lead time at the current speed (**Measure**: the research estimates 22-173 Mbit/s at 40 mph depending on density; lower LODs and SH degree load first, finer levels fill in as the rider approaches). `bytes` per segment, tier and LOD level let the app plan prefetch and show download sizes. Full pre-download uses the same files.

## 4. Rider path (`path.json`, Proposed)

**Source of truth: the reconstructed rig pose.** For each rig frame, take the reference point from the aligned COLMAP rig and drop it along local gravity onto the **reconstructed road surface** (a local surface fit to the splats or SfM points under the rig). That accounts for lean and steering yaw, and keeps the path on the road the rider sees. The RTK antenna track is the pose prior and the QA reference, not the path.

1. Build the road-contact trace for the whole build.
2. Smooth it as a **frequency and wavelength** problem: attenuate lateral and yaw content above about 0.5 Hz (rider weave is 0.5-2 Hz), avoid a response peak near 0.2 Hz, and filter vertical by wavelength at the maximum design speed. Keep curvature continuous and yaw acceleration bounded (about 30 deg/s^2, **Measure**).
3. Separately enforce a registration bound: smoothed path within about 0.1 m of the road-contact trace (**Measure**).
4. Heading from the tangent; no pitch, no roll; the path carries no head or bike motion. The app adds a fixed seated eye height and the tracked head pose, and renders a cockpit rest frame (forward-gaze MVP).
5. Slice into segments and sample every 0.5 m: `s, e, n, u_road, heading_rad, curvature_1pm`.

Where RTK is float or missing, the reconstruction (constrained by fixed epochs either side) carries the path; the gap is flagged (section 7).

## 5. Distance profile (`profile.json`, Proposed)

The profile publishes **primitives**, not tuning policy. Sampled every 1 m of route-global 3D distance, computed per build then sliced.

| Channel | Meaning |
|---|---|
| `s`, `s_h` | Route-global along-surface and horizontal distance (m) |
| `lat`, `lon` | Datum as in manifest, 1e-7 deg or better |
| `h_ortho`, `sigma_h` | Orthometric height of the road surface and its 1-sigma |
| `grade` | **Canonical physics grade** (%), rise over horizontal run, lightly smoothed (about 10 m window, **Measure**). Drives virtual speed, so every rider, ghost and leaderboard shares one grade |
| `curvature` | From the rider path (yaw rate at speed v is v times curvature) |
| `corridor_left_m`, `corridor_right_m` | Steerable half-widths, measured from what was captured (single pass: about ±1 m) |
| `surface` | Road surface class for rolling resistance (manual tag for now) |
| `quality` | Flags: RTK float or none, bridged gap, weak splat coverage |

**Moved to the app** (with a reference implementation in a shared library, and defaults recorded in `route.json`): the trainer command filter (smoothing by time and speed, rate limit, quantisation, change points limited to the FTMS 1-2 Hz write rate), device clamps, difficulty and descent scaling, and comfort speed in bends. These depend on runtime speed, the trainer's response and the rider, so retuning them must never mean republishing routes. `yaw_rate_per_mps` and `grade_ctrl` are dropped from the profile.

## 6. Splats (Proposed, numbers are **Measure**)

- **Training blocks:** about 100 m with 25-50 m overlap, cameras chosen by what they see, trained in gsplat (Apache 2.0): MCMC with `cap_max`, antialiased mode, dynamic-object and rider masks, a road-plane or depth prior, a separate sky model.
- **Appearance:** one appearance and exposure reference per session; blocks are matched over their overlaps; a seam colour-delta gate checks every cut. Exposure compensation uses PPISP only after a licence and patent check of the gsplat implementation; the fallback is the bilateral grid. No Inria, CC-NC or NVIDIA-research-licensed code in the product path; a licence review precedes any commercial build.
- **Ownership:** each trained block is trimmed so every splat belongs to exactly one block, by 3D proximity to the block's own cameras (not arclength alone).
- **Floaters:** culled by defect class (near-path floaters, stereo-inconsistent splats, oversized soft splats), always excluding a clearance zone above the reconstructed road surface so road and kerb are never deleted.
- **Density:** budgeted by region (near road and verge vs far field), not per route metre, and set from measured frame time.
- **LOD:** `splat-transform` Streamed SOG (chunks about 16 m), Quest and PCVR tiers, environment layer separate. **Cross-level blending is a pass/fail gate:** neither PlayCanvas nor Spark documents crossfade between LOD levels, so popping at riding speed is tested before the package format is frozen.
- **Quest budget:** the only Quest measurement (PlayCanvas, about 18 ms per million splats) implies about 0.3-0.5M splats at 72 Hz and 0.25-0.4M at 90 Hz. Refresh rate is chosen by the app at runtime (Quest exposes 72/90/120); the proposal is 90 Hz minimum, 120 Hz where the scene allows, dropping detail rather than frames.

## 7. QA (Proposed)

Two layers:
- **Pipeline QA** (offline, per build): writes `qa/<build_id>/` outside the package, even on failure. Gates are **per segment** with severity (fail, warn), numeric thresholds and a versioned threshold set. A failing segment blocks its own publication and lands on a recapture list; the rest of the route can publish with that stretch marked.
- **Runtime QA** (on headset, per renderer release and on sample routes): frame-time p99 at the target rate, LOD popping and sort flicker at riding speed, re-base hitches. These can't be seen offline.

Initial pipeline gates (all thresholds **Measure**, starting values shown):

| Gate | Fail when |
|---|---|
| Ingest integrity | Any photo can't be timed or positioned (slices 2-3) |
| Registration | p95 camera-prior residual on fixed epochs > 5 cm; scale error > 0.05 % |
| Path to road | p95 vertical offset > 3 cm or lateral > 5 cm |
| Path comfort | Yaw acceleration above budget; lateral or vertical content above 0.5 Hz above a set level |
| RTK gaps | Longest non-fixed run > 50 m unless bridged, or grade error across a bridged gap > 0.5 % |
| Seams | Colour delta across a block or session seam above a set level; any duplicate splat across owners |
| Floaters | Splats inside the near-path clearance volume |
| Coverage | Forward and side yaw bins along the path below a coverage threshold (rear excluded until rear cameras exist) |
| Corridor | Published corridor wider than the measured captured band |

## 8. Multi-session routes (Proposed)

Multi-session is the normal case: at the measured walking pace of field test 1 (1.29 m/s) an 80 km pass is about 17 hours, and even at the 2 m/s capture ceiling it is about 11 hours.

- Route-global `s` runs across every session; each segment records its `session_id`.
- **Capture rule:** each session runs 50-100 m past its planned join, so the next session re-covers that stretch.
- **Joins:** the overlap is trained as a join block with per-session appearance, then trimmed. The join is placed at a natural break where possible, and gated by the seam colour and geometry checks.
- Passes from different sessions are not merged into one reconstruction; the shared earth anchor makes the geometric join exact, and appearance is handled at the join.

## 9. Capture implications (for Olly)

- One pass per route; the corridor is about ±1 m, measured per segment. Wider corridors (side to side on small roads) are an experiment for later.
- Field test 2 includes a second session over part of the route and one deliberate lateral pass, to measure joins and off-line quality (details to agree before the test).
- The capture rate is about 1.3-2 m/s, so routes are multi-day; field test 2 fills in a capture and processing cost model (hours, TB and GPU-hours per finished km). The 20-80 km scope gets a go/no-go on that model.
- Overcast light is strongly preferred.
- Rig config must include the antenna position and each camera relative to a rig reference point, and enough to drop the rig to the road (section 4).
- Rear-facing cameras after the MVP; the coverage gate will then include the rear.

## 10. MVP validation plan (so the design can fail early)

1. Cut the 1 km route into 3-4 segments of 250-400 m and fly through every join with re-basing at speed.
2. Build a synthetic 80 km package by repeating MVP segments to test streaming, cache behaviour and storage.
3. Synthetic hairpin and out-and-back fixtures for ownership, neighbours and prefetch.
4. Quest 3 go/no-go against a perceptual quality bar written before measuring; fallback is PCVR as the product tier and Quest as a preview tier.
5. Renderer bake-off (PlayCanvas vs Spark) including the LOD blending gate and frame time at 72/90/120 Hz.
6. Lateral quality at 0.5, 1 and 2 m offsets using the deliberate lateral pass.
7. Grade on the KICKR CORE 2: response to the physics grade through the app's reference filter.

## 11. Open decisions

1. Renderer: PlayCanvas proposed, confirmed by the bake-off.
2. Segment length, block length, density budgets, Quest refresh target: measured on field test 2 and the headset.
3. PPISP licence and patent check (fallback: bilateral grid).
4. Trainer bridge transport (phone app, desktop app or ESP32): an app decision; the profile assumes no transport.
5. 20-80 km go/no-go after the cost model.
