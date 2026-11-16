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
- Slice 1 done (review: `research/reviews/slice1-review.md`, fixes committed).
- Slice 2 done (review: `research/reviews/slice2-review.md`, fixes committed). Logger counter policy: `pulses_lost` is an error, the rest are warnings (their effects are checked in the data). 
- Slice 3 done (review: `research/reviews/slice3-review.md`, fixes committed). Known residual risk: a missing first photo plus an extra last photo with no file-number gap and repeating intervals is undetectable (documented in photos.py). Next: slice 4.
- Slice 4 done (review: `research/reviews/slice4-review.md`, fixes committed). Real-data note: on field test 1, Hermite and linear predict held-out epochs about equally (median 2-3 cm) because measurement noise and 0.4 s aliasing dominate at walking pace; Hermite matters at cycling speed. Next: slice 5, rig config and lever arm.
- Slice 5 done (review: `research/reviews/slice5-review.md`, fixes committed). Carry into slice 6: on a straight stretch the antenna track can't fix rotation about the direction of travel, so the lever arm (about 0.25-0.36 m) is unobservable from positions alone. A whole build with bends is fine; long straight segments need another constraint (gravity from camera up vectors or the road plane, or the rig's nominal attitude). Also for slice 6: add a cam_from_rig helper expressed relative to a reference camera (COLMAP rig convention).

Track 1 (slices 0-5) complete on `pipeline/track1`, not pushed.

## PPK check on field test 1 (2026-11-13)

- Inputs are complete for PPK: `receiver.ubx` has 1415 RAWX epochs (about 51 measurements each, one gap over 0.25 s), and `rtcm.bin` carries MSM4 for GPS/GLO/GAL/BDS from a single fixed base (one 1005 position, about 12.5 km from the walk).
- The six float stretches (3.4-20.6 s) are not caused by correction latency or satellite loss: corrAge and numSV (median 30) are the same as in fixed epochs. That is the pattern a combined forward-backward PPK pass usually bridges, because the backward pass enters each stretch with ambiguities already fixed. Likely beneficial, not yet measured.
- PPK run (RTKLIB rtklibexplorer demo5 755e637, its `f9p_ppk.conf`: kinematic, combined forward-backward, L1+L2, GPS+GLO+GAL+BDS, fix-and-hold, base position from RTCM 1005):
  - At 5 Hz it reports 92% fixed against 82% live, but the extra fixes are largely false. Where both are fixed they agree to a few cm until the last 33 s, then differ by about 0.30 m north and 0.31 m up. An ambiguity-free check (integrated Doppler velocity) sides with the live fix: +3.78 m vertical change by Doppler, +3.88 m live, +4.50 m PPK over epochs 1100-1300. PPK also has 53 epoch-to-epoch vertical jumps over 15 cm, against 2 live.
  - With the rover decimated to 1 Hz (`-ti 1`) PPK fixes only 3.5-6%. The base sends MSM at 2 Hz for GPS and BDS and 1 Hz for GLO and GAL, below the rover's 5 Hz.
  - Caveats from the deepseek review: the Doppler adjudication is clearest vertically; horizontally over the last 33 s it leans to the live fix but less decisively; one 90-120 s cluster of PPK's extra fixes does agree with Doppler to 0.1 m. The PPK inputs and commands are in the session scratchpad, not the repo.
  - Verdict: on this data PPK is not beneficial; the live F9P RTK is the more reliable solution. Don't build a PPK stage now. Keep logging RAWX and RTCM as insurance and revisit only if field test 2 has long float stretches (an own 1-5 Hz base would be the stronger option then).
  - Method lesson: judge PPK by consistency checks (Doppler, epoch jumps, agreement where both are fixed), never by fix rate alone.

## Cross-model review of track 1 (2026-11-13)

- Deepseek red team: `research/reviews/track1-deepseek-review.md`; triage: `track1-triage.md`; verification of the fixes: `track1-fixes-review.md`. All "fix in track 1" rows are now fixed and tested (commits 4fa2e8d to 78389ad), 257 tests.
- Decisions taken: a changing base reference position warns (a VRS may move with the rover) and real discontinuities are caught by the Doppler jump check; the 0.25 m jump threshold is calibrated on a walk and must be re-checked on cycling data; invalid TM2 rows only refuse when a photographed pulse needs them.
- Deferred nits: float photos keep a prior at the receiver's hAcc and gaps don't inflate sigma (revisit with COLMAP in slice 6); the falling-edge check allows 0-1 s rather than about 50 ms; photo files aren't hashed into provenance; the stopped-early threshold is an absolute 10 pulses.
- Process lesson: scripted edits must assert that each replacement matched (three tests had silently not landed in the slice 5 fixes).

## Field test 2 preparation (2026-11-14)

- Camera serials (from test shots): cam0 / GP6 363073016649, cam1 / GP7 013070043618, cam2 / GP8 353073007602, cam3 / GP9 023071108155. The 100D writes `SerialNumber` (read by the pipeline) but no Canon `FileNumber`, so ordering falls back to filenames.
- Focus after refocusing: cam0 2.49-2.96 m, cam1 and cam3 2.13-2.49 m, cam2 2.96-3.73 m (all beyond the 2.1 m hyperfocal at 18 mm f/8). cam0 had a screw-on filter, now removed.
- Bench test in time mode (sessions 20261114T121925Z and 20261114T122323Z, copied to `~/rigcheck/bench/`): cam1's shutter plug was loose in the first run (0 of 58 exposures); after reseating, all four fired on all 16 pulses after warm-up 0, delays 90-103 ms; cam2's on/off test confirmed the GP8 mapping; `match` 16/16 per camera and `ingest` 60 images, serials read correctly, base station 3527.
- Pico restored to distance mode afterwards (device config identical to the repo).

## Field test 2 (2026-11-14)

- Sessions `20261114T133719Z` (loop, about 200 x 110 m) and `20261114T134727Z` (about 100 m offset pass); data in `~/fieldtest2` (cards in `cam0..3`, split by hard links into `s1/` and `s2/`, outputs in `out/`). Rig config `rigs/handlebar-4cam-ft2.toml` measured after the ride (cameras level, side cameras 50 degrees off ahead, antenna base as reference, 1.18 m above the road).
- RTK fixed 99.7% (session 1) and 100% (session 2). Ingest: 2464 images (session 1), 234 (session 2), with `--camera-fault 1`.
- Problems found and handled: the F9P sent 11 garbled TIM-TM2 messages (count 0, stale rising time), now recovered from Pico ticks and confirmed by the raw falling edge (`recovery.py`); cam1 hit Canon Err 80 and lost its last 9 exposures in session 1 and took nothing in session 2 (`--camera-fault`).
- Logger issues to fix later: the first pulse after a stop of 5-20 s is missed by every camera (cameras doze; add a warm-up pulse after a pause); 13 KB of RTCM dropped at the UART; the F9P TIM-TM2 count-0 quirk is new since field test 1.
- Before the next ride: find cam1's Err 80 cause (fresh battery and card, long bench run, swap hot-shoe adapter and cable).
- Next: slice 6, COLMAP with priors, on session 1.

## Slice 6 progress (2026-11-15)

- Built (branch `slice-6`): `colmap-prepare` (CPU SIFT from full resolution, one OPENCV camera per folder, first-pass priors with the lever arm under a level rig facing the RTK velocity), spatial matching, mapping with priors, prior refinement with prior-aware bundle adjustment, a gated report (`colmap_report.py`), rig calibration from the reconstruction (`calibrate.py`), and `colmap-run` / `rig-calibrate`.
- Performance: COLMAP's default SIFT first octave of -1 made extraction about 30 times slower on this Mac; first octave 0 on all cores gives about 0.3 s per photo.
- Subset (pulses 100-250, 604 photos): one model, all registered, 0.98 px; 8269 verified pairs including strong cross-camera pairs.
- The hand-measured side-camera yaw was 50 degrees but the photos say 40.5 and 39.8 (probably measured from the bar); calibration fixes it, and the optical centre sits about 24 mm behind the lens front. After calibration: horizontal prior residual p95 4.1-4.5 cm (passes), rig agreement 0.6-1.3 cm and 0.03 degrees.
- Open: vertical prior residual p95 about 6 cm, from one stretch (pulses 136-158) where the reconstruction sits up to 8 cm below RTK heights that Doppler supports; that stretch has weaker ties (about 1860 points per photo against 2600-3000). Full session (loop closure) running to see whether it persists.

## Slice 6: full session 1 and lessons (2026-11-15)

- Olly: prioritise accuracy and correctness over speed; production runs on a cloud GPU. Speed-ups only where verified not to cost accuracy.
- Incremental mapping of 2464 photos on this Mac ran 4.5 h without finishing, then was stopped for memory (two mappers in parallel on 16 GB was my mistake). The global mapper registered all 2464 in one model in 60 min; fitted to the priors by RANSAC Sim3, then refined.
- Full session 1 (global): reprojection 0.97 px, scale against the RTK path 0.99993, rig within 0.6-1.3 cm and 0.04-0.12 degrees, vertical prior residual p95 4.1-4.5 cm (loop closure fixed the subset's dip), horizontal p50 2.3-2.5 cm but p95 5.6-6.1 cm (gate 5 cm), with no mean bias.
- The horizontal tail sits in three stretches with visible causes, not RTK (hAcc 14-15 mm throughout): close ivy hedge and bare branches (pulses 138-151), parked vans and cars close to the cameras (430-468), and a sharp turn at a junction with plain tarmac (589-597).
- Lessons for the pipeline: vehicle masking should also remove bad matches; the gate should report weak stretches rather than one route-wide p95; refine's full bundle adjustment took about an hour per round, so a point-sampled refine is under test against the full result; Auto ISO gives visibly different exposure between cameras, so capture should fix exposure across cameras.

## Requirement: hardware-configurable pipeline (Olly, 2026-11-15)

Adding cameras, changing camera type or changing rig must be a config change, not a code change. Already config: rig layout, folders, serials, positions, angles, antenna, curtain travel, calibration. Still hard-coded, to move into the rig config in a dedicated slice after slice 6: the 4-camera count (session, matching, rig validation, CLI; more cameras also needs a logger v2 contract), the COLMAP camera model per camera, Canon-specific rules (IMG_9999 rollover, 30-500 ms shutter delay, exposure limits), and the reference camera (cam2).

Done 2026-11-16 (branch `config`, plan `docs/PLAN-CONFIG.md`): the camera count comes from the `camN_delay_us` columns of `pulses.csv`; rig schema 1.1.0 adds `[camera_defaults]` and per-camera overrides (COLMAP model, shutter delay range, exposure limit, file number wrap) and `reference_camera`. The session keeps only generic sanity limits (delay 30 ms to 2 s); camera-specific limits are checked when photos are matched. The field test 2 rigs now say `reference_camera = 2`; field test 2 ingest and the COLMAP report are unchanged apart from the rig file hash. More than 4 physical cameras still needs a logger change.
Review (2026-11-16) fixes: cameras the rig leaves out get its `[camera_defaults]`; unknown-camera errors name their source (rig, `--camera-fault`, photo folder); `reference_camera` must be an integer; a non-table rig section refuses; shutter delay ranges must sit within the logger's 30 ms-2 s; Canon FileNumber decoding stays folder x 10000 whatever the filename wrap. Known limits: a camera whose filenames roll over to 0000 rather than 0001 isn't configurable yet; without `--rig`, every `camN` folder present is read, so a stray folder for a camera the session didn't record now refuses.

## Refine: sampled vs full, judged on held-out pulses (2026-11-16)

Same global model and first-pass priors, every 10th pulse held out (248 photos):

| | Time | Held-out horizontal p50 / p95 | Held-out vertical p50 / p95 | Scale vs RTK path |
|---|---|---|---|---|
| Sampled (200k longest tracks) | 75 min | 1.75-2.08 / 3.7-5.2 cm | 1.28-1.35 / 3.4-4.0 cm | 0.99984 |
| Full (979k points) | 179 min | 2.17-2.47 / 5.6-6.5 cm | 1.33-1.43 / 3.9-4.1 cm | 0.99994 |

Sampling costs no accuracy on held-out pulses and is slightly better, plausibly because the longest tracks are the most reliable matches and short tracks carry the bad ones (vegetation, reflections). Adopted as the default. Caveats: a single held-out pulse sits between priored neighbours 0.8 m away, so it tests local consistency more than independence (holding out whole stretches would be stronger); the path-length scale ratio is noise-biased at the 1e-4 level. Gate: only cam0 horizontal p95 (5.2 cm) fails on the sampled run.

## Slice 7 build (2026-11-16)

Built on branch `slice7` (not pushed and not merged): `georoute.path` (rig pose per pulse, robust road contact, separate horizontal and vertical smoothing, 1 m profile), `georoute.geoid` (OSGM15), `georoute.package` (segment frames, 3D neighbours, atomic publication), `georoute.qa` (per-segment gates and plots), `georoute.build` and the `route-build` CLI, plus `schemas/route|path|profile-1.0.0.json`. 446 tests pass; `ruff check .` clean.

### Real run

```
cd ~/geo-registered-COLMAP
uv run georoute route-build ~/fieldtest2/colmap/s1-gsample \
  --images ~/fieldtest2/out/s1-v120/images.json \
  --rig rigs/handlebar-4cam-ft2-calibrated.toml \
  --out ~/fieldtest2/routes --route-id ft2-s1 --segment-length 300
```

- The first attempt with `out/s1/images.json` refused on schema `georoute.images/1.1.0`; the file already carried `velocity_enu`. The Splat pipeline session regenerated it at 1.2.0 as `out/s1-v120/images.json` and verified its images and frame are identical to `out/s1`. The strict 1.2.0 check was kept.
- An intermediate run exposed a QA indexing bug: raw road-contact distance was used as smoothed path arclength (509.478 m against 508.539 m). Fixed test-first in `0929964`: each trace sample now carries `trace_smoothed_m`; profile flags, segment gates and RTK runs use it, and raw distance remains only the spline parameter. The refusal QA reports from both attempts are retained under `qa/ft2-s1/`.
- The comfort gate was then corrected in `156c473`: speed is no longer capped pointwise at each sample, but follows a reachable profile limited by 1.5 m/s^2 longitudinal acceleration forward and backward; yaw acceleration is the time derivative of v*kappa along that profile. The earlier build at `20261116T100323Z` is superseded; its QA is retained.
- Final output package: `~/fieldtest2/routes/ft2-s1/20261116T103407Z/` (provenance commit `bef3cc9`, the anchored local comfort smoothing). The earlier `100323Z` and `100813Z` builds are superseded; their QA is retained.
- QA: `~/fieldtest2/routes/qa/ft2-s1/20261116T103407Z/` with `report.json`, `plan.png`, `profile.png` and `curvature.png`.
- Route: 508.54 m, loop false (start and end 19.95 m apart), ETRS89, OSGM15 grid sha256 `bf0ac81f1c0c33332693d7d606f335b793c3c2995b8e69eeb1350bd94adcde3b`, two segments, each the other's neighbour. `splats` is empty until slice 8.

### Gate table

| Segment | s range (m) | Vertical p95 (gate) | Lateral p95 (result) | Lateral max (gate) | Comfort max yaw accel (s) | comfort_met | iterations | Longest non-fixed | Max bridged gap | Registration | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| seg_0000 | 0-300.00 | pass 2.81 cm | 9.50 cm | pass 17.57 cm | fail 90.2 deg/s^2 at 274.6 m | false | 1 | pass 2.0 m | pass 0.0 m | fail cam0 5.2 cm | fail |
| seg_0001 | 300.00-508.54 | fail 3.58 cm | 10.34 cm | pass 24.28 cm | fail 150.0 deg/s^2 at 344.9 m | false | 1 | pass 0.0 m | pass 0.0 m | fail cam0 5.2 cm | fail |

Both segments are on the recapture list and carry `gate-failed` in the published segment quality. Lateral p95 is reported but not gated; vertical p95 stays at 3 cm and lateral max at 0.5 m. The comfort gate uses 18 m/s, capped in bends by 3 m/s^2 lateral acceleration and 1.5 m/s^2 longitudinal acceleration.

- Height above road: 625 measured contacts, 0 nominal; fitted p5 1.124 m, p50 1.160 m, p95 1.215 m against the 1.18 m rig config. Median is 2.0 cm below the hand measurement and p95 3.5 cm above it.
- Road points: 978,646 input, 932,315 used (track length at least 3, reprojection error at most 2 px).
- Smoothing: horizontal parameter 1.2512, p95 lateral 9.97 cm, max 24.28 cm, comfort_met false, 1 iteration; vertical parameter 0.1442, p95 vertical 2.92 cm; no FITPACK warnings.
- RTK: 2,456 of 2,464 images fixed and 8 float; 2 profile samples flagged `rtk-float` in seg_0000; 41 images flagged `time-recovered`, giving 10 profile samples in seg_0001. No bridged gaps and no nominal-surface samples.
- Corridor: 1.0 m each side, marked unmeasured until the lateral pass is analysed.

### Plots

- `plan.png`: road-contact trace against the smoothed path, with the segment boundary marked.
- `profile.png`: orthometric height and grade against distance.
- `curvature.png`: signed curvature with the design-speed lateral limit (0.0093 1/m at 18 m/s and 3 m/s^2) and the beyond-limit regions shaded.
- Curvature has sharp spikes around s = 275, 344-348, 419-425 and 471-480 m despite a smooth-looking plan view. Those spikes drive the comfort failures.

### Kink analysis against the RTK antenna

Both tracks are in the session ENU frame and are compared before smoothing, one antenna position per pulse. A constant offset is removed per stretch, then the residual is projected across the road-contact tangent. All figures are centimetres unless stated. Pulse numbers and route s are different counters; the context column gives the pulse numbers.

| Stretch s (m) | Pulses | Samples | Max lateral difference | RMS lateral | Max curvature (1/m) | Max dkappa/ds (1/m^2) | Context |
|---|---|---|---|---|---|---|---|
| 270-280 | 338-349 | 11 | 1.3 | 0.5 | 0.106 | 0.046 | none known |
| 344-348 | 429-433 | 5 | 1.2 | 0.8 | 0.329 | 0.232 | start of the parked-vehicle stretch, pulses 430-468 |
| 419-425 | 522-529 | 7 | 1.3 | 0.9 | 0.229 | 0.120 | none known |
| 471-480 | 587-597 | 10 | 1.8 | 1.1 | 0.287 | 0.151 | junction turn, pulses 589-597 |

The antenna and the road-contact trace agree laterally to 1.2-1.8 cm in the three kink stretches, so the kinks are real rider motion, not reconstruction noise. Olly pulled over a couple of times to let vehicles pass; the 344-348 kink starts at the first parked-vehicle pulses and the 471-480 kink overlaps the junction turn. The 419-425 kink is another real lateral movement.

### Comfort outcome on the real kinks

The comfort-first pass ran one iteration and accepted no candidate: every candidate that would bring a kink below 30 deg/s^2 needed more than the 0.5 m lateral departure cap. The final path therefore equals the 0.10 m p95 base fit. The candidate figures are from a single-window anchored target at the base s, using the mildest weight factor.

| Kink (m) | Peak s (m) | Final local max yaw | Final max lateral departure | Best candidate yaw | Candidate departure | Absorbed |
|---|---|---|---|---|---|---|
| about 274 | 274.6 | 90.2 deg/s^2 | 10.1 cm | 8.6 deg/s^2 | 75.0 cm | no, cap |
| about 345 | 344.9 | 150.0 deg/s^2 | 23.6 cm | 132.8 deg/s^2 | 70.6 cm | no, cap |
| about 420 | 418.6 | 140.8 deg/s^2 | 10.5 cm | 45.6 deg/s^2 | 54.8 cm | no, cap |
| about 475 | 470.9 | 87.8 deg/s^2 | 24.3 cm | 79.7 deg/s^2 | 59.3 cm | no, cap |

The 274 m kink could be made comfortable at the cost of 75 cm of departure, which is outside the captured band; the others need 55-71 cm and two still exceed the comfort limit at that departure. None is absorbed within the cap, so the real route remains a recapture signal rather than a publishable comfortable path.

### Deviations from the plan

- QA is written to `qa/<route_id>/<build_id>` rather than the design's `qa/<build_id>`, so two routes built in the same second cannot overwrite each other's reports.
- `overview.json` is deferred to slice 8 with the overview tiles.
- The real build uses `out/s1-v120/images.json` for the schema reason above; the strict 1.2.0 check has no compatibility path.
- Per review decisions: horizontal smoothing is comfort-first with a 0.5 m lateral departure cap, vertical keeps a 0.03 m p95 bound, lateral p95 is reported rather than gated, and a long non-fixed RTK run is a warning unless it contains a bridged gap. All gate thresholds are recorded as Measure.
- The trace-placement fix above was found by the real run and is now covered by a heavy-weave regression test.
- Horizontal smoothing is the comfort-first anchored local target described above: a local window around each violation is re-targeted to the trace with lowered weights while the rest is anchored to the current fit, s stays fixed, the departure cap is 0.5 m, lateral p95 is reported rather than gated, and thresholds are 1.1.0. The gate table above is from that build.

### Open questions

- Comfort: the real kinks are not absorbed within the 0.5 m departure cap; the candidates that would fix them need 55-75 cm. This may mean the cap, the 22 m window, or the fixed base s is too restrictive for these capture pull-overs, or that the route genuinely needs recapture. Two separated 0.6 m synthetic pull-overs also cannot meet comfort within the cap, while a single 0.6 m pull-over over 5 m and two 0.45 m pull-overs can; the window size is the first thing to test if the design changes.
- Registration: the known cam0 horizontal prior p95 of 5.2 cm is route-wide, so every segment fails registration. Per-camera or per-stretch reporting would localise it.
- Path to road: seg_0001 is just over the p95 gates (3.58 cm vertical, 10.34 cm lateral) while seg_0000 passes. The smoothing bound is global; decide whether gates should use a global p95 or the path should be smoothed per segment with continuity constraints.
- Grade shows peaks around plus or minus 15 percent in the 10 m window; check whether they are real road grade or noise before the app uses them for speed.
- The loop endpoint separation of 19.95 m is a capture fact, not a pipeline fault; no loop flag was set.
