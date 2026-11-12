# Pipeline rework: status and resume notes

Brief: `obolland/gps-camera-trigger` `docs/PIPELINE-HANDOFF.md`. Product reference (not gospel): Olly's `vr-cycling-agent-handoff.md` (5 Nov 2026, local file, not in git).

## Decisions from Olly (2026-11-12)

- The VR app isn't built yet and comes after the pipeline. Output formats (`path_cam.json`, tile manifest, ENU origin) are ours to design; no backward compatibility needed.
- Drop the legacy GPX/EXIF-timing workflow entirely.
- Splatfacto training runs on Olly's AWS cloud GPU. The local Mac (M1 Pro, 16 GB, COLMAP 4.1.1 without CUDA) can't train. POC COLMAP/Nerfstudio versions unknown.
- Real images wait for field test 2 (4 cameras, about 1 km). Slices needing real images are gated on it.
- App requirements: as many modern headsets as possible (none chosen); frame rate as high as possible; sickness is the biggest risk, realism is the main lever; PlayCanvas or three.js; rider stays on the captured line, optionally with a steering riser (e.g. Elite Sterzo Smart) giving lateral movement within a road-width corridor; speeds 10-20 mph typical, up to 40 mph downhill; real routes 20-80 km, multi-session routes useful if not too costly; wide trainer support, dev trainer Wahoo KICKR CORE 2; app will have map/minimap, distance markers, later other riders and Strava export; ultra-realism is the quality bar.
- Olly expects technical questions to be researched, not asked.

## Audit of the POC (2026-11-12)

Verified by reading the code and running checks:

1. `generate_path_cam_from_aligned_colmap.py`: SubSec parsing drops leading zeros (`"09"` becomes 0.9 s, not 0.09 s), misordering photos within a second (field test 1 IMG_0837/0838).
2. README Step 3 passes `--camera_tz`; argparse rejects it; tz hard-coded to Europe/London. (Moot: EXIF timing is being removed.)
3. README layout drift: `run_colmap_reconstruction.sh dataset/images` writes `dataset/colmap/sparse/0`; README and `ns-train --data dataset/keyframes` expect `keyframes/`.
4. README Step 9 converts COLMAP sparse `points3D.ply`, not the trained splat; no `ns-export`; `0_enu` already renamed; tool installed is `splat-transform`.
5. `drop_teleports` compares against the last kept point, so one early outlier can silently drop the rest.
6. Umeyama reflection branch flips R but uses `sum(S)` not `trace(DS)` for scale (matters for near-straight, near-flat routes).
7. Linear GPX interpolation clamps out-of-range times silently.
8. Missing GPX `<ele>` silently becomes 0.
9. Only `sparse/0` used; split reconstructions ignored silently.
Also: per-image OPENCV intrinsics; `alignment_max_error 3` too loose for RTK; no aligner success check; `enu_origin.json` assumes WGS84 without a datum field; duplicated COLMAP readers; no deps manifest or tests.

COLMAP 4.1.1 here has `rig_configurator`, `pose_prior_mapper`, `sequential_matcher`, `global_mapper`, `ImageReader.single_camera_per_folder`, and all POC options.

Proposed: keep and test ENU maths, Umeyama (diagnostic, fix 6), resample/smooth/grade (fix 1, 5), manifest generation, no-normalisation training rule; replace readers with pycolmap; remove GPX/EXIF/dt/JS/duplicate script; replace the shell reconstruction with Python orchestration.

## Own finding: one ENU origin can't serve a long route

Earth curvature below an origin's tangent plane: 2 m at 5 km, 31 m at 20 km, 126 m at 40 km. A single-origin "up" axis biases grade by about d/R (0.6% at 40 km), which the trainer would apply. float32 resolution is 1 mm at 10 km, 4 mm at 40 km, 8 mm at 80 km. Direction: per-tile local frames anchored to one shared earth-fixed reference; grade and elevation from each point's own geodetic height.

## Research (branch `research/output-design`)

- `runtime-rendering-and-headsets.md`
- `tiling-lod-and-training.md`
- `trainer-and-sensors.md`
- `comfort-and-sickness.md`

All four files are complete and committed.

## Next steps

1. Done: proposed output design in `docs/OUTPUT-DESIGN.md` (v0.1, not yet challenged).
2. **Don't run the thinking skill to challenge the output design until Olly gives the go-ahead.**
3. Plan track 1 (slices 0-5: repo hygiene, characterisation tests, session ingest, photo matching, exposure time and Hermite positions, rig config and lever arm). These need only the field test 1 sample.
4. Push nothing without Olly's explicit go-ahead.

## Design challenge (2026-11-12)

- Claude red team done: `research/design-challenge-claude.md` (synthesis, section 5 lists proposed edits), raw panel in `research/redteam-claude/`.
- Deepseek peer session ("splat pipeline deepseek") is red-teaming independently into `research/design-challenge-deepseek.md` (uncommitted until this session commits it).
- Merged review applied: `docs/OUTPUT-DESIGN.md` is now v0.2 (2026-11-12). Next: plan track 1 (slices 0-5).

## Olly's answers to the merged review (2026-11-12)

- Delivery: aim for streaming first; keep download-before-ride as an option, not the plan.
- Rear view: MVP is forward gaze only (cockpit rest frame); rear-facing cameras come after the MVP.
- Corridor: one capture pass only, so about ±1 m; ±1 m is fine for the MVP. Experiment later with wider (ideally side to side on small roads), within reason.
- Route scope: agreed that 20-80 km routes are multi-session builds, with a go/no-go after field test 2 costs are known.
- Field test 2: Olly can include a second session over part of the route and a deliberate lateral pass; discuss details before he goes.
- Refresh rate: Olly asked which is less nauseating and whether it is configurable (answer: higher and stable is better; it is app-selectable on Quest, so it is a runtime budget, not a pipeline decision).
- Headset: Olly has none and will buy one for development.

## Plan approval (2026-11-12)

- Track 1 plan (`docs/PLAN-TRACK1.md`) approved: package `georoute`, Python 3.12, COLMAP upgrade to 4.2.1 allowed.
- Warm-up photos: dropped (they still take part in pulse matching, then are filtered out).
- Photos without RTK fixed (my decision, Olly delegated): keep every capture photo for reconstruction; each image's position prior carries its own uncertainty from hAcc/vAcc (float = loose prior, no fix = no prior), so SfM bridges float stretches between fixed ones; QA gates on long non-fixed runs.
- Real-time RTK never revises past epochs. PPK can: field test 1 `rtcm.bin` contains MSM4 for GPS/GLO/GAL/BDS (1074/1084/1094/1124) plus 1005/1033/1230, i.e. full base observations, and `receiver.ubx` has RAWX/SFRBX. A forward-plus-backward PPK pass (RTKLIB) is therefore feasible and becomes an optional ingest stage after slice 4. Caveat: `LOCAL` may be a virtual base whose 1005 position changes on reconnect; handle per connection.

## Build progress (branch `pipeline/track1`)

- Slice 0 done (review: `research/reviews/slice0-review.md`, fixes committed).
- Slice 1 done (review: `research/reviews/slice1-review.md`, fixes committed). Next: slice 2, session ingest.
