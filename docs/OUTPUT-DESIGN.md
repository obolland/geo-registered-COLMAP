# Pipeline output design (proposal, v0.1)

Status: proposed 2026-11-12, not yet challenged or agreed. Grounded in `research/` (four topic files) and the audit in `research/STATUS.md`. Labels: **Decided** (Olly's requirement or verified constraint), **Proposed** (my design choice), **Measure** (needs an experiment on the 1 km MVP before the number is fixed).

## 1. What the outputs must serve

| Consumer | Needs | Source |
|---|---|---|
| Renderer (PlayCanvas first, Spark as fallback) on WebXR, Quest 3 baseline, PCVR high tier | Tiled splats with LOD, small local coordinates, one global splat budget, prefetch along a known route | runtime research 6 |
| Rider motion and comfort | A smooth, level, wobble-free reference path; curvature, yaw-rate and comfort-speed profiles | comfort research 5 |
| Trainer (FTMS grade, 0.01 % resolution, about 1-2 Hz writes) | Distance-indexed elevation and a smoothed control grade with change points | trainer research 2, 9 |
| Steering riser | Corridor half-widths limited to what was actually captured | tiling research 4, trainer 9 |
| Map, minimap, distance markers, Strava export | Lat/lon per sample, route overview, distance along route | trainer research 8 |
| Pipeline QA and Olly | A per-route report that gates publishing | handoff "fail loudly" |

## 2. Coordinate frames (Decided in principle, details Proposed)

A single ENU origin can't serve a 20-80 km route: the ground drops 126 m below the origin plane at 40 km, a single "up" biases grade by up to 0.6 %, and float32 only resolves about 4 mm at 40 km. All three research topics reached the same conclusion independently.

- **Earth anchor:** everything is ultimately expressed in ECEF (float64). Input datum is whatever the corrections deliver (today ETRS89 via Point One `LOCAL`), recorded explicitly, never assumed WGS84.
- **Segment frame:** the route is cut into **segments of about 1 km** of route length. Each segment has its own local ENU frame: origin = a point on the route near the segment middle (lat, lon, ellipsoidal h, plus its ECEF position in float64), axes = local east, north, up at that origin. Every splat, path sample and bound inside a segment is stored relative to that origin, so coordinates stay within about 600 m (float32 resolution about 0.06 mm).
- **Joining segments:** the transform between two adjacent segments is derived exactly from their ECEF anchors (a rigid transform, rotation about 0.01 degrees per km). The app keeps a floating origin near the rider and re-bases at segment boundaries.
- **Vertical:** grade and elevation are computed from each sample's own geodetic height, never from a frame's U axis. Displayed and exported elevation is orthometric, with the geoid model named in the manifest (OSGM15 in the UK; EGM2008 elsewhere). Proposed.
- **Export datum:** Strava and map lat/lon are reported in the stated datum. ETRS89 and WGS84 (current realisation) differ by under 1 m in Europe in 2026, which is harmless for maps and export; the manifest says which one it is. Proposed.
- **Axes:** data stays ENU, right-handed, z-up. The one conversion to the renderer's y-up frame happens in the segment transform at runtime, not in the data. SOG's own axis convention is recorded per asset. Proposed.

## 3. The published route package

```
<route_id>/<build_id>/
  route.json                       manifest (versioned, the app's only entry point)
  overview.json                    whole-route polyline at 10 m (lat, lon, h_ortho, s) for map and minimap
  segments/<seg_id>/
    profile.json                   1 m samples along the segment (section 5)
    path.json                      rider reference path in segment frame (section 4)
    quest/  lod-meta.json + chunks Streamed SOG, Quest tier
    pcvr/   lod-meta.json + chunks Streamed SOG, PCVR tier
    env.sog                        sky and far-field environment layer
  qa/report.html, qa/report.json   publish gate (section 7)
```

Not published, kept for reprocessing: per-block lossless masters (3DGS PLY, float32, full SH), the COLMAP model, ingest outputs and the run log. A Spark `.RAD` package can be generated from the same masters if the renderer bake-off favours Spark, so the engine choice stays reversible.

### `route.json` (Proposed)

```json
{
  "schema": "vrcycle.route/1.0.0",
  "route_id": "...", "build_id": "...", "created_utc": "...",
  "datum": "ETRS89", "geoid": "OSGM15", "distance_m": 1012.4, "distance_kind": "3d",
  "loop": false,
  "segments": [
    {
      "seg_id": "s000", "s_range": [0.0, 1012.4],
      "origin": { "lat": 51.15884, "lon": -4.12424, "h_ell": 207.77, "ecef": [x, y, z] },
      "bounds_local": { "min": [e, n, u], "max": [e, n, u] },
      "profile": "segments/s000/profile.json", "path": "segments/s000/path.json",
      "splats": {
        "quest": { "format": "sog-streamed", "url": "segments/s000/quest/lod-meta.json", "splats_by_lod": [], "sh_degree": 1 },
        "pcvr":  { "format": "sog-streamed", "url": "segments/s000/pcvr/lod-meta.json",  "splats_by_lod": [], "sh_degree": 3 },
        "env": "segments/s000/env.sog", "antialiased": true
      },
      "corridor_half_width_max_m": 1.0,
      "provenance": { "sessions": ["20261108T153731Z"], "pipeline_commit": "..." }
    }
  ]
}
```

Every file carries a schema version; JSON Schemas live in this repo and the app validates against them. Profile and path files are JSON for the MVP; switch to a binary columnar format if size or parse time shows up in measurement (80 km at 1 m is about 80k samples per channel).

## 4. Rider reference path (`path.json`, Proposed)

The rider is not placed on COLMAP camera centres (four cameras interleave and carry rig wobble). The path is built from the **RTK antenna track** at each exposure, mapped through the rig config to the road surface below the bike:

1. Hermite-interpolated antenna positions (ingest slice 4) minus the measured antenna height above the road gives a road-surface trace.
2. Fit a C2-continuous, zero-phase smoothing spline in the segment frame. Max deviation from the raw trace under about 0.1 m (**Measure**).
3. Heading from the spline tangent; curvature continuous; yaw acceleration bounded (about 30 deg/s^2 proposed, **Measure**). No pitch, no roll, no vertical content above about 0.5 Hz.
4. Sample every 0.5 m: `s, e, n, u_road, heading_rad, curvature_1pm`. The app adds a fixed seated eye height plus tracked head pose; the path itself carries no head or bike motion.

Because COLMAP is aligned to the same RTK frame, the path and the splats register by construction. QA checks it (section 7).

## 5. Distance profile (`profile.json`, Proposed)

Sampled every 1 m of along-surface distance `s` (3D; horizontal `s_h` also stored).

| Channel | Meaning |
|---|---|
| `s`, `s_h` | Along-surface and horizontal distance (m) |
| `lat`, `lon` | Datum as in manifest, 1e-7 deg or better |
| `h_ortho`, `sigma_h` | Orthometric height and its 1-sigma (from RTK vAcc and fix state) |
| `grade_geom` | Rise over horizontal run (%), about 5 m window, for visuals and stats |
| `grade_ctrl` | Trainer grade (%): zero-phase smoothing over 10-25 m (**Measure** on the KICKR CORE 2), quantised to 0.1 %, unclamped |
| `curvature`, `yaw_rate_per_mps` | From the reference path; yaw rate at speed v is v times curvature |
| `comfort_speed_mps` | Speed above which this bend exceeds the yaw-rate or lateral budget (**Measure** budget) |
| `corridor_left_m`, `corridor_right_m` | Steerable half-widths, limited to the captured band |
| `surface` | Road surface class for rolling resistance (manual tag for now) |
| `quality` | Flags: RTK float or none, splat coverage weak, interpolated gap |

Plus `grade_ctrl_changes`: precomputed `[s, grade]` change points (|delta| >= 0.2 %), so the app writes FTMS only when the grade changes. Device clamps, difficulty and downhill scaling stay in the app.

At 3 cm vertical noise, grade noise is about 0.4 % over 10 m and 0.2 % over 20 m, so RTK fixed is good enough for the control grade; float sections get flagged and smoothed harder.

## 6. Splats (Proposed, numbers are **Measure**)

- **Training blocks:** about 100 m of route, 25-50 m overlap each side, cameras chosen by what they see. gsplat directly (Apache 2.0), not Splatfacto: MCMC with a `cap_max` from the density budget, antialiased mode, appearance and exposure compensation (PPISP plus EXIF), dynamic-object and rider masks, a road-plane or depth prior, separate sky model. No non-commercial code (Inria, CC-NC, NVIDIA research) in the product path.
- **Seams:** trim each trained block to its own distance interval so every splat belongs to exactly one block; overlap during training is what makes the cut seamless.
- **Floaters:** remove splats inside a tube around the rider path (about 1.5-2 m from the eye trajectory) and penalise near-camera density in training.
- **LOD and packaging:** join a segment's trimmed blocks and run `splat-transform` to Streamed SOG with LOD chunks of about 16 m. Two tiers: Quest (finest level capped near 5k splats per metre, reduced SH) and PCVR (full). Sky and far field go in `env.sog`.
- **Budgets to measure on the MVP:** splats and bytes per metre, Quest frame-time p99 at 72/90/120 Hz against splat budget, LOD popping at 18 m/s, quality at 1, 2 and 3 m lateral offsets. Research estimates (not measurements): Quest 0.3-1M rendered splats; 22-173 Mbit/s at 40 mph depending on density, which points to downloading a route before riding rather than live streaming.

## 7. QA report (publish gate, Proposed)

Non-zero exit and no publish if a gate fails. Per route:

- Ingest integrity (from slices 2-3): counts, unmatched photos, timing and fix quality per image.
- Registration: prior vs reconstructed camera centre per camera, per segment; path-to-splat check (road surface under the path within a tolerance).
- Path comfort: max yaw acceleration, vertical jitter spectrum above 0.5 Hz, deviation from the RTK trace.
- Grade: `grade_ctrl` vs a long-baseline RTK grade; float or no-fix stretches.
- Splats: splats and bytes per metre per tier, near-path floater count, a flicker metric over a rendered fly-through at riding speed.
- Corridor: captured lateral band vs `corridor_*_m`.

## 8. Multi-session routes (Proposed)

A route is an ordered list of segments, and each segment records which sessions it was built from. Passes from the same session (or within about an hour) are reconstructed together; different days become separate builds, because shadows, wet roads and parked cars can't be reconciled. Joining segments from different sessions then needs only the shared earth anchor (section 2); the visible appearance change at the join is the open risk, so joins should sit at natural breaks. Nothing extra is needed in the MVP beyond the provenance field.

## 9. What this means for capture (for Olly, not pipeline work)

- **Capture speed ceiling:** 0.8 m spacing with the 0.4 s minimum interval caps capture at about 2 m/s. An 80 km route is about 11 hours per pass.
- **Lateral quality:** steering more than about 1 m off the captured line degrades quickly. A wider corridor needs 2-3 same-day passes across the lane.
- **Light:** overcast is strongly preferred; no appearance model removes hard shadows.
- **Rig config must include** the antenna height above the road and its offset from each camera (the path in section 4 depends on it).

## 10. Open decisions

1. Renderer: PlayCanvas proposed; a one-week Quest 3 bake-off against Spark should confirm before the app starts.
2. Segment length (1 km) and training block length (100 m): **Measure** on field test 2.
3. Quest density cap, SH degree per tier, and LOD transition behaviour: **Measure**.
4. `grade_ctrl` window and comfort budgets: tune on the KICKR CORE 2 and in short comfort rides.
5. Whether the trainer bridge is a phone app, desktop app or ESP32: an app concern, flagged so the profile format doesn't assume a transport.
