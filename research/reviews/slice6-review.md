# Slice 6 independent review (branch slice-6, head 6d1725a)

Scope: `git diff main..slice-6 -- src tests`, plan `docs/PLAN-SLICE6.md`, facts `research/colmap-4.2.1-facts.md`, the subset workspace `/Users/olly/fieldtest2/colmap/subset` (read only; the full run in `colmap/s1` was not touched). Scratch experiments are under `/private/tmp/claude-501/-Users-olly/3cdacf84-f22a-4ca5-bf6a-a42ab6847ecf/scratchpad/` (e1..e8.py). `uv run pytest`: 305 passed. `ruff check` clean; `ruff format --check` flags only `research/colmap-4.2.1-facts.md` (its embedded python block), not src or tests.

Note on side effects: opening the subset `database.db` read-only with sqlite3 left an empty `database.db-shm` and `database.db-wal` there (11:00). They are harmless and can be deleted.

## Verdict

The geometry is right and consistent end to end (details in section A). The mapping, refine and calibration pipeline works on real data. What is wrong is mostly around the edges: one bug that will break the next session with a no-fix epoch, a gate that can pass vacuously, a calibration offset that is biased, and stale-output and silent-drop hazards. None of this changes the subset numbers materially.

## Blockers

### B1. Images without a prior keep COLMAP's placeholder prior, and the mapper then throws

`write_priors` only replaces priors for images named in `priors`. Feature extraction on real photos writes a placeholder prior for every image (UNDEFINED coordinate system, NaN position; the real subset DB has one per image). An image with `has_prior = false` (no-3d-fix, jump, or carr_soln 0) therefore keeps an UNDEFINED prior beside the CARTESIAN ones.

Evidence (scratchpad/e7.py): synthetic scene, one prior replaced by a default `PosePrior()` placeholder, then `reconstruct`:
`ValueError [database_cache.cc:508] Check failed: coordinate_systems.size() <= 1 (2 vs. 1) Inconsistent coordinate systems defined in pose priors`.
Deleting that image's prior entirely maps fine (12 of 12 registered). The full session s1 has 0 images without a prior (and 8 float), so the run in progress is unaffected, but any session with one no-fix or jump photo will die at the mapping step. The test `test_prepare_extracts_features_per_camera_folder_and_writes_priors` passes because its synthetic JPEGs have no EXIF, so no placeholder exists; that is why this slipped through.

Fix: in `write_priors` (or `prepare`) delete every pose prior whose image is not being given one: read all, `clear_pose_priors()`, rewrite the kept ones. Add a test that writes a placeholder for an image absent from `priors` and asserts `read_all_pose_priors` has no UNDEFINED entry, plus a `reconstruct` test on a scene where one image has no prior.

### B2. The gate can pass with nothing checked

`gate` only fails on a present, above-threshold value. Verified (scratchpad/e2.py):
- `rig_check({}, rig, 2)` returns `{"axial_offset_m": 0.0, "cameras": {}}`, and `gate({"cam0": {p95 None}}, that)` returns `[]`.
- Drop every cam2 pose (the reference) and `rig_check` again returns no cameras, so the rig check silently passes. Same if any single camera has no pulse in common with the reference: it is simply absent from `cameras`.
- A camera with zero RTK-fixed images has `horizontal_p95_m = None` and is skipped. A camera with one pulse is judged on one sample.
- `colmap-run` exits 0 on `failures == []`, so this is the exit code the plan's "done when" rests on.

Fix: make `gate` take the rig and expected cameras. Fail when: a rig camera (other than the reference) is missing from `rig["cameras"]` or has fewer than N pulses (say 20); the reference has no poses; any camera has `images == 0` or a None p95; registered fraction or model count is not met (re-check `mapping` in the report, since `report()` can be run on a model made elsewhere). Test each.

## Should-fix

### S1. The calibrated optical-centre offset is biased because it is estimated with the hand rotations

`calibrate_rig` computes `rig_check(poses, rig, reference)["axial_offset_m"]` with the hand rig, whose side-camera yaw is 9.5 degrees wrong, so the lens axes in the model are wrong. Evidence (scratchpad/e2.py, subset poses):
- offset with hand rotations: 20.3 mm (what the calibrated file records, `axial_offset_m = 0.0203`);
- offset with hand positions and calibrated rotations: 24.7 mm (matches the "about 24 mm" in STATUS);
- `rig_check` of the calibrated rig reports a residual `axial_offset_m` of 4.3 mm, in `report.json`.

So, to the "applied twice" question: it is not applied twice in one pass. `rig_check` always refits the offset, so translation residuals do not depend on whether the rig is calibrated. But a calibrated rig should report about 0, and it reports 4.3 mm because the first estimate was made with the wrong axes. A second `rig-calibrate` then moves the positions another 4.3 mm (S2). The committed `rigs/handlebar-4cam-ft2-calibrated.toml` therefore has positions about 4 mm short along each lens axis.

Fix: in `calibrate_rig`, build the rig with the calibrated rotations (`dataclasses.replace` on the cameras), then estimate the offset with that; ideally add a `[calibration]` check that a second pass reports below 1 mm. Test: poses with a known yaw error and a known offset, assert the recovered offset is within 1e-4 (the existing `test_rig_check_recovers_the_optical_centre_offset` has no rotation error, so it cannot see this).

### S2. Re-calibrating a calibrated rig destroys the hand measurements

`write_calibrated_rig` writes `hand_*` from whatever the source file's `position_in_rig_m` and `yaw_pitch_roll_deg` are. Verified (scratchpad/c2.toml, from `calibrate_rig(subset, ...-calibrated.toml, ...)`): name becomes `handlebar-4x100d-ft2-calibrated-calibrated`, `hand_position_in_rig_m` now holds the previous calibrated values, the original hand numbers are gone, and the positions move another 4.3 mm.

Fix: if the source already has `[calibration]`/`hand_*` keys, either refuse ("calibrate from the hand-measured file") or keep the existing `hand_*` and start from them. Test it.

### S3. Stale outputs survive a re-run, so a failed run can leave a passing report

`prepare` deletes only `database.db` (and `-shm`, `-wal`). `sparse/`, `sparse/refined`, `mapping.json`, `matching.json`, `report.json` and `priors.json` from an earlier run stay. Consequences:
- `report` chooses `refined` if that directory exists, so after a fresh `reconstruct` whose `refine` then fails (RigError "did not settle", or too-straight track), a later `report` reads the old refined model against new priors.
- If `colmap-run` stops at a `ReconstructionError` or a refine error, the old `report.json` (possibly with `failures: []`) is still on disk and looks current.
- `sparse/1`.. from an earlier multi-model run remain beside the new `sparse/0`.
- `-journal` is not in the stale list (only `-shm` and `-wal`). Probably moot, since COLMAP's database uses WAL, but cheap to add.

Fix: `prepare` removes the derived outputs (`sparse`, `mapping.json`, `matching.json`, `report.json`, `priors.json`) before starting; `reconstruct` clears `sparse/` first; `refine` writes into a temp directory and renames, so `refined` always matches `priors.json`. Test: prepare over a workspace containing a stale `report.json` and `sparse/refined`.

### S4. A photo that fails to extract is silently dropped

Verified (scratchpad/e8.py): delete one JPEG after writing `images.json`, run `prepare`. It returns `images: 7` (of 8), no error, and `priors.json` has 6 records including the phantom name. Only the later 95 percent registration check could notice, and only against the DB count. Fix: after extraction compare `db.read_all_images()` names with the requested names and raise listing the missing ones; build `priors.json` from the DB's images, not from `priors`.

### S5. `refine` ignores the adjuster's outcome and whether the priors were used

`_prior_bundle_adjustment` calls `adjuster.solve()` and discards the returned summary, which has `termination_type` and `is_solution_usable()` (checked on `BundleAdjustmentSummary`). Per the facts file, the pose-prior adjuster silently drops the priors and fixes the gauge when it cannot align to them (fewer than 3 priors, or alignment fails); `alignment_ransac.max_error` defaults to 0.0 here. I measured that on the subset the priors are active (a second BA pass moves centres by under 5e-6 mm, and `sparse/0` vs `refined` moves 1.6 cm median, 2.9 cm max), so this is a guard, not a live bug.

On the specific questions: the adjuster refines poses, points, focal length and extra (distortion) params, not the principal point (library defaults). The gauge is fixed by the priors, and I found no drift: re-running it on `sparse/refined` converges in 29 iterations to 0.65167 both times and the cameras' intrinsics change by about 1e-9. The prior loss in `refine` is `TRIVIAL` (the default `PosePriorBundleAdjustmentOptions`), unlike `reconstruct`, which sets the robust loss; a bad RTK epoch is then only protected by its sigma.

Fix: raise when `summary.is_solution_usable()` is false or fewer than 3 images carry priors; set `prior_position_loss_function_type` to robust in `refine` to match `reconstruct`, or document why not. Test with a model with 2 priors.

### S6. Calibration has no quality gate and succeeds on nothing

`calibrate_rotations` records `spread_deg` and `pulses` but never refuses on them. With no poses (`calibrate_rotations({}, rig, 2)`) or with the reference missing, it returns only the reference's hand values, and `calibrate_rig` then writes a "calibrated" rig with the hand angles and offset 0.0. Also, only the 15 degree disagreement is checked. A camera with 3 pulses and 5 degrees of spread is accepted.

The 15 degree refusal itself is sensible (a mix-up, not a measuring error), but the message gives no way forward; mention what to do (re-measure, or pass a larger limit).

Fix: raise `CalibrationError` when a rig camera has under N pulses (say 20) or median spread above about 0.5 degrees, and when the reference has no poses; test each.

### S7. `write_calibrated_rig` is brittle

It hand-writes TOML from `doc[...]`:
- `doc["name"]`, `doc["measured"]`, and each camera's `role`, `folder`, `serial` are indexed directly, but `load_rig` treats them as optional, so a valid rig without `serial` gives `KeyError`.
- Strings are written as `f'"{value}"'` with no escaping, so a description containing a quote or backslash produces invalid TOML (the `[reference] description` is free text).
- Comments, unknown tables, and ordering in the source are lost; `out_path == rig_path` overwrites the hand file.

Fix: use `.get` with `load_rig`'s defaults, escape with `json.dumps` (valid TOML basic strings for these cases) or use `tomli_w`, and refuse `out == source`. Add a round-trip test where the source has no serial and a quote in the description.

### S8. `report.json` lacks provenance and two plan items

Missing from the output: the rig file name and `sha256` (the subset report was clearly produced with the calibrated rig but nothing says so), the gate thresholds, and the reference camera. Plan step 5 also asks for registration per camera and "scale against RTK distance"; neither is in `report.json` (only the overall `unregistered` list in `mapping`). There is also no standalone report command: `report()` is only reachable through `colmap-run`, so re-checking a finished workspace against a calibrated rig needs the Python API (that is evidently how the subset report was made).

Fix: add `rig: {name, sha256}`, `gates`, per-camera registered counts, and RTK path length vs reconstructed path length; add a `colmap-report WORKSPACE --rig` command. The residual in `prior_residuals` is also partly circular (the priors are in the final BA with sigma about 1.4 cm), which is worth one line in the report.

### S9. CLI error handling is uneven

- `rig-calibrate` catches `(RigError, CalibrationError, OSError)`. A workspace without a model raises `ValueError` from pycolmap (`reconstruction.cc:995 ... files do not exist`, verified for report, refine and calibrate, scratchpad/e4.py), which is not caught, so it prints a traceback. A `priors.json` from an older version (no `pulse` or `fixed`) gives a bare `KeyError`. `colmap-run` catches ValueError (so the missing-model case is fine there) but not `KeyError` or pycolmap `RuntimeError`.
- `--pulses`: `"5"` raises `IndexError` in `prepare`, `"1-2-3"` silently uses the first two, `"-5-9"` is misparsed. Parse with a regex and `parser.error`.
- No version check on `priors.json`: add `"version"` and a clear error naming the missing keys.

### S10. `images.json` schema was not bumped

`ingest.py` adds the required `velocity_enu` but `IMAGES_SCHEMA` stays `georoute.images/1.1.0`. An older `images.json` (still valid by version) makes `prepare` die with `KeyError: 'velocity_enu'`. Bump to 1.2.0 and have `prepare` check `data["schema"]` and give a message ("re-run ingest").

## Nits and observations

- N1. Geometry for the reference-camera gauge (question 1): sound for relative calibration; what is lost is the rig frame's absolute orientation, which stays at the hand-measured cam2 (yaw 0, pitch 0.6). That error enters the lever arm as `|p - l| * angle`: about 2 to 6 mm per degree for cameras 0.12 to 0.37 m out, so 3 cm at 5 degrees. At the present level of agreement it is fine; say so in the calibrated file's header, and keep cam2 the reference only while it is the best-measured camera.
- N2. `yaw_pitch_roll` near pitch 90: exact round trip at 80, 89, 89.9999 and 90 (scratchpad/e2.py), but at exactly 90 yaw and roll are not separately defined and the exact result is luck of the floating point (`arctan2` of two near-zero terms); a noisy rotation near 90 returns arbitrary yaw/roll. A handlebar camera will not be near 90, and the 15 degree gate bounds it, so only add a guard: raise when `abs(m[2, 1]) > 1 - 1e-9`.
- N3. Gate uses medians for the rig check (`translation_residual_m`, `angle_error_deg`), so a few bad pulses are invisible. Report p95 and max beside the median (not necessarily gated).
- N4. First-pass priors assume the rig faces the velocity. When pushing, reversing, or crabbing in tight turns, forward is up to 180 degrees off, which for a 0.37 m camera is up to 0.7 m, against a covariance of 1.4 cm plus 3 cm. Mapping then relies on the robust loss to reject those priors. Consider a larger `LEVER_ALLOWANCE_M` below a speed (say 1 m/s) or when the heading rate is high, or exclude priors below `MIN_HEADING_SPEED_MPS`. Not needed for s1 (median speed 1.2 m/s), but 0.3 m/s is a low cut.
- N5. `_headings` builds an N by M matrix (`np.abs(order[:, None] - moving[None, :])`): 6 million entries for s1, but quadratic for long sessions. Use `np.searchsorted` on the sorted `moving` indices.
- N6. `refine` writes `sparse/refined`, `priors.json` and the database in three steps with no atomicity; a crash leaves them inconsistent (see S3 for the rename fix).
- N7. `refine` clears all database priors and writes only the registered images, so unregistered images lose theirs. Fine for now; note it.
- N8. Vertical residual (open item in STATUS): measured on the subset and not explained by attitude (correlation of cam2 vertical residual with pitch -0.10, roll 0.16), nor by a constant timing offset (regressing the residual on velocity gives no consistent lag across E, N, U). It exists before refinement (p95 vertical 5.9 to 6.0 cm in `sparse/0` against the final priors, 6.0 to 6.4 cm after), so `refine` neither causes nor fixes it. It is common to all four cameras (a rig-level shift) and sits in pulses 140 to 155 (mean -5.9 and -3.5 cm), exactly where the antenna height crests at 2.7 m. That points at the RTK height path through the crest (the Hermite interpolation with Doppler velocity, or a real local effect) more than at the model. A cheap check: compare the raw PVT heights with the interpolated ones at those photo times, and run the stretch with `sigma_v` inflated to see whether the model keeps its shape.
- N9. Tests: `test_refine_moves_priors_from_the_antenna_to_the_cameras` is a real synthetic COLMAP scene and does catch a transposed rotation, but uses one camera, a zero lever arm and an identity attitude, so lever-arm sign and multi-camera attitude are not covered. Untested: `report()`, `_model_poses`, `calibrate_rig` (including the offset path), multi-model refusal in `reconstruct`, `colmap-run` and `rig-calibrate` end to end (the CLI test only parses arguments), prepare with EXIF placeholders, `gate` with None or missing cameras. The `velocity_enu` direction is covered in `test_trajectory` (and I confirmed it on the real data: median difference from the position derivative 0.049 m/s at 1.2 m/s).

## A. Geometry check (question 1), what I verified

- `nominal_world_from_rig`: columns are right = forward x up, forward, up; determinant +1; matches x right, y forward, z up in ENU.
- `velocity_enu`: `v_ecef @ ecef_from_enu` is `R^T v` (rows times the matrix), which is the ENU velocity; on s1 it matches the antenna position derivative to 0.05 m/s.
- `camera_centre` = a + R_world_from_rig (p - l). In `refine`, `align` returns `cam_from_world().rotation.matrix().T` (= world_from_camera) and `refine_priors` uses `world_from_rig = world_from_camera @ rig_from_camera.T`, correct since `rig_from_camera` maps camera to rig. `_model_poses` uses the same R^T and `projection_center()`.
- `rig_check`: `r_ref.T @ (c_cam - c_ref)` is in the reference camera frame; the nominal vector is converted with `ref_from_rig = rig_from_camera(ref).T`; the axial model `v = t - offset * (axis_cam - axis_ref)` with `axis = rig_from_camera[:, 2]` (the lens direction) is a correct least-squares fit for one scalar. Relative rotation `r_ref.T @ r_cam` against `ref_from_rig @ rig_from_camera(cam)` is consistent. Real data agrees: with the calibrated rig the angle errors are 0.03 to 0.04 degrees.
- `calibrate_rotations`: `rig_from_ref @ r_ref.T @ r_cam` = rig_from_camera of the camera; the mean is a chordal (SVD) mean, sound; positions move by `-offset * rig_from_camera[:, 2]`, consistent with the lens-front model (cam0 moved 13 mm in x and 15 mm in y for 20 mm along a 40 degree axis, as expected).
- Prior handling: priors are CARTESIAN with a diagonal 3x3 covariance in ENU metres; no lever allowance in `refine`, correct since the rotation is then known; 8 of 604 subset images are float but carry sigma about 1.7 cm.
