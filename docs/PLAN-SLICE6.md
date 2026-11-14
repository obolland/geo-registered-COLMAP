# Plan: slice 6, COLMAP reconstruction with position priors

Status: proposed 2026-11-14. Inputs: `docs/OUTPUT-DESIGN.md` v0.2, `research/colmap-4.2.1-facts.md` (checked against the installed COLMAP and pycolmap 4.2.1), field test 2 ingest outputs (`~/fieldtest2/out/s1`), rig config `rigs/handlebar-4cam-ft2.toml`.

## Scope

Turn ingest's timed, positioned photos into a metric COLMAP reconstruction in the session's ENU frame, with per-camera intrinsics, camera priors corrected for the antenna lever arm, and a residual report that gates the result. Session 1 of field test 2 is the target. Out of scope: rider path, profile and splats (slices 7-8), joining sessions, rig-constrained bundle adjustment beyond an experiment.

## Facts this rests on (see the facts file for sources)

- Priors live in the database's `pose_priors` table per image, written with `pycolmap.PosePrior` and `Database.write_pose_prior`, CARTESIAN or WGS84, with a 3x3 covariance.
- `pycolmap.incremental_mapping` with `use_prior_position = True` (the pose_prior_mapper) returns a metric model in the priors' frame. A missing covariance falls back to 1 m, so every prior gets an explicit one.
- `spatial_matcher` pairs images by prior distance, which covers cross-camera pairs and the loop's start/end overlap without a vocabulary tree.
- `single_camera_per_folder` (pycolmap `CameraMode.PER_FOLDER`) gives one intrinsics set per camera; the 100D isn't found in COLMAP's sensor database, but its EXIF focal-plane resolution (5798.66 px/in) gives an initial focal of about 4109 px at full resolution.

## Steps (small, each tested and reviewed)

1. **Workspace and priors (`georoute colmap-prepare`)**
   - From `images.json`: write an image list (`cam0/IMG_x.JPG` paths) and a database.
   - Feature extraction through pycolmap: OPENCV model, one camera per folder, CPU, SIFT at the default 3200 px.
   - Priors in the session ENU frame (CARTESIAN). First-pass priors already apply the lever arm using a **nominal rig attitude** (heading from the RTK velocity, rig level), which puts them within about 2 cm instead of 37 cm. Their covariance is the image's prior sigma plus a lever-arm allowance. Images with `has_prior = false` get no prior.
   - Tests: priors and covariances written as expected on a synthetic database; nominal-attitude lever arm against the rig maths.
2. **Matching**: `spatial_matcher` on the priors (about 30 neighbours within 15 m, tuned on the subset). Report pair counts per camera pair, so a camera that matches nothing is visible.
3. **Mapping**: `incremental_mapping` with prior positions and robust prior loss. Refuse if more than one model results or under 95% of images register (thresholds tuned on the subset, recorded).
4. **Lever-arm refinement**: feed each image's reconstructed rotation into `rig.refine_priors`, update the priors, and re-run the bundle adjustment with priors until the priors settle (expect 1-2 rounds, since the first pass is already close).
5. **Residual report and rig check** (the gate):
   - Prior versus reconstructed centre, per camera, p50 and p95, horizontal and vertical, on fixed epochs.
   - Registration per camera, and the list of unregistered images.
   - Mean reprojection error, and scale against RTK distance.
   - Relative pose of each camera to the others at the same pulse against `rig.toml` (2 cm and 2 degrees), and the optical centre's distance behind the lens front, which the rig config left open.
6. **Experiment, not default**: the same run with COLMAP rig constraints (`rig_configurator`, frames by pulse), compared on residuals. The cameras fire up to about 15 ms apart, so this stays an experiment.

## How it's built and checked

- Steps 1-5 are built test-first on synthetic data (a small synthetic pycolmap scene with known cameras), then run on a **subset** of session 1 (about 150 pulses, 600 photos) to tune the thresholds, then on the full session. The real COLMAP runs are exploratory, so they're checked by the report rather than by unit tests.
- Each step gets an independent review, as before.

## Done when

- Session 1 reconstructs as one model with at least 95% of images registered.
- Prior residual p95 is at most 5 cm on RTK-fixed epochs.
- Per-camera rig geometry agrees with `rig.toml` within 2 cm and 2 degrees, or the report says which camera disagrees and by how much.
- The report is written, with a command you can run.

## Risks

- **Runtime on the Mac CPU** for 2464 photos: probably a few hours per full run. The subset keeps development fast; the full run can move to the AWS GPU.
- **Hard surfaces:** vegetation and smooth tarmac match poorly, which may leave gaps; the report will show where.
- **Exposure differences** between cameras (Auto ISO) could weaken cross-camera matches. That's an appearance issue for slice 8, but worth watching here.
- **Untested in practice:** pose_prior_mapper with rigs (step 6 only).
