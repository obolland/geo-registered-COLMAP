# Slice 5 review: rig config and lever arm

Commits 064b2ad and c2c0b5c on pipeline/track1. `pytest` 181 passed, `ruff check` and `ruff format --check` clean. Probe scripts were run from the scratchpad; no repo files were changed.

Verdict: the geometry conventions, composition order and lever-arm signs are correct. The main problems are (1) `refine_priors` reports "settled" on straight rides while being 0.35-0.45 m wrong, (2) `measured` and several config fields are not validated and some bad inputs give tracebacks, (3) `slerp`, `refine_priors`, `camera_centre` and `reference_point` are unused outside tests, and spec details are only partly met.

## Blocker

None for slice 5 as a self-contained library. B1 below becomes a blocker for slice 6 if it is not handled before COLMAP is plugged in.

### B1 (blocker for slice 6). refine_priors "converges" to a wrong answer when the ride is straight

Evidence: with the repo's own synthetic aligner (60 frames, 8 m/s, 5 degree lean, noise-free) on a straight track at heading 0.3, 1.0 and 2.0 rad, and 2 cm antenna noise, `refine_priors` returns after 2 iterations with a worst camera-centre error of 0.35, 0.36 and 0.34 m. Noise-free it was 0.45 m at heading 0.3 and 2.0 (it was right only at heading 1.0, by luck of the SVD null space). The test only uses a bend. Cause: the first-pass priors are antenna positions, which are colinear on a straight ride, so a similarity fit leaves rotation about the track axis undetermined. Rig roll is then wrong, the lever arm (0.2 m up) is rotated about the wrong axis, and the next prior is consistent with the wrong rotation, so the 1 mm change test passes. `model_aligner` has the same degeneracy. The stopping rule measures self-consistency, not correctness.

Fix: in `refine_priors` (or a helper slice 6 calls first) check the spread of the antenna track before iterating: raise `RigError` (or return a flag recorded in the report) when the second singular value of the centred antenna positions, or the heading range, is below a threshold (for example under about 2 m of lateral extent, or under about 20 degrees of heading change over the build). Add a test with a straight ride that expects the refusal. Document in the docstring that the lever arm is unobservable on a straight ride.

## Should-fix

### S1. Config validation holes (strictness and error type)

Probed by editing the example file:
- `measured = "false"` loads as `measured == True` (`bool(...)` of a non-empty string). This flips the one flag that says geometry is placeholder. Require `isinstance(v, bool)`.
- `[timing] curtain_travel_ms` or `[reference] height_above_road_m` missing gives a bare `KeyError`; `height_above_road_m = "tall"` gives a bare `ValueError`. The CLI catches only `RigError`/`OSError`, so these are tracebacks. A non-UTF-8 file gives `UnicodeDecodeError` (not caught by `rig-check`).
- No cameras at all loads fine (`rig.cameras == ()`).
- `curtain_travel_ms = -5` and `nan` are accepted; `nan` timing would poison every `t_mid`. Require finite and `>= 0` (and a sane upper bound, say 20 ms).
- Vectors accept `true`, `nan` and `inf` (`bool` is an `int`, and `isinstance(v, int | float)` passes). Pitch 400 deg is accepted. Require `math.isfinite`, exclude bool, and bound pitch to [-90, 90].
- Two cameras at identical positions, duplicate non-empty serials across cameras (`"A"` twice), and a folder such as `"../x"` or an absolute path are accepted. Duplicate serials are only caught later if photos carry them. Check serial uniqueness and that folder is a plain name.
- `serial = 12345` (TOML integer) is silently stringified; leading zeros are not at risk with a quoted string (`"0012345"` kept, and `_same_serial` strips zeros), but an unquoted `0012345` is a TOML syntax error with a message that does not mention quoting. Require `serial` to be a string and say so in the message; the example file should say "quote it".
- `role`, `name` are not type-checked (`str()` of anything).

Fix: one `_number`/`_bool`/`_string` helper family raising `RigError` with the table and key; wrap `UnicodeDecodeError` in `load_rig`; require at least one camera; add parametrized cases to `test_invalid_configs_refuse` for each.

### S2. Missing or unreadable --rig file gives a traceback in match and ingest

`_rig(args)` calls `load_rig`, which does `path.read_bytes()`. `_match` catches `(MatchError, RigError)` and `_ingest` catches `(IngestError, MatchError, TrajectoryError, RigError)`; neither catches `OSError`, unlike `rig-check`. `georoute match S I --rig missing.toml` raises `FileNotFoundError`. Fix: catch `OSError` in both (or have `load_rig` convert `OSError` to `RigError`, which also fixes it everywhere). Add a test.

### S3. Wiring tests do not cover serials, precedence or the null case

`test_ingest_takes_folders_serials_and_curtain_travel_from_the_rig` has a rig whose serials are all empty, so the serial path in `photo_sources` is never exercised; the stubs cannot carry MakerNote serials (known), but a record-level test of `photo_sources` returning `{0: "..."}` and skipping empty serials is cheap. Also untested: `--curtain-travel-ms 0` overriding a non-zero rig value (the `is None` logic is right, but it is the precedence that was asked for), and `images.json["rig"] is None` without a rig. Add these.

### S4. slerp, refine_priors, camera_centre and reference_point are not used anywhere

`grep` shows no caller outside rig.py and tests. That is expected for slice 5 (slice 6 consumes them), but two spec items are therefore unmet by anything executable: "each camera at its own t_mid (attitude interpolated by slerp)" and "per-camera time offsets are honoured". `refine_priors` takes already-computed per-image antenna positions and uses each image's own aligned camera rotation, so slerp is not on its path at all; per-camera offsets are honoured only because the test builds a distinct heading per camera (`cam.index * 0.012`) and the caller supplies the antenna position at each image's t_mid. Nothing in the function enforces or tests it. Decide and record whether slerp is needed: the loop as written does not need it (rotation comes from each image's own alignment). Either delete slerp until a caller exists, or add the missing piece (rig attitude at the t_mid of an image from neighbouring frames) with a test where two cameras share a frame time but have different t_mid. Unused code with its own edge cases (S5) is a maintenance cost.

### S5. slerp edge cases

- Duplicate times: `u = 0/0` gives NaN with a RuntimeWarning and a silent NaN matrix when rotations differ (observed warning on `times=[0,0,1]`).
- Unsorted times, a single time (`len(times) == 1` gives `i = -1`, returns something without error), and `len(times) != len(rotations)` are not validated.
- Quaternion extraction takes signs of x, y, z only from the antisymmetric part. At exactly 180 degrees that part is zero and the signs are lost: the rotation `[[0,-1,0],[-1,0,0],[0,0,-1]]` (180 about (1,-1,0)) round-trips with max error 2.0 (i.e. a different rotation); the sibling about (1,1,0) happens to work. Exact for 179.9 degrees and below, and a random-quaternion round trip is exact to 1e-12. Realistic attitude steps between exposures are far from 180 degrees, so this is low risk, but it is a latent bug in a general-looking helper. Fix: use the standard branch on the largest of trace and diagonal (Shepperd), or `scipy.spatial.transform.Slerp` if scipy is acceptable as a dependency. Sign flipping (`q0 @ q1 < 0`), endpoints and midpoint were verified correct, including a 180 degree midpoint.

### S6. Phase-centre labelling in the example file is not honest

The comment says "no primary source yet, so measured as zero". Nothing was measured; it is assumed. The slice plan said the offset comes from the u-blox data sheet, cited, or else becomes a measured rig value. Reword to "assumed zero until measured or cited" and keep it out of any wording that implies measurement (the file also has `measured = false`, so the file as a whole is honest, but this line is not). The magnitude is centimetres against a 0.2 m arm, so the default is acceptable; the F9P reports the L1/L2 phase centre, so the offset is a small vertical term only if the ARP is to be the reference. Note the labelling `arp_in_rig_m` (rig reference point to ARP) plus `phase_centre_offset_m` (ARP to phase centre, in the rig frame) is correct and `lever_arm_m = sum` is the vector from the reference point to the phase centre, as `c = a + R (t - l)` needs.

### S7. refine_priors: the semantic of the convergence test, and loss of information

- It measures the change between successive priors, not the error to truth; see B1. In the noise-free test the true error after 2 iterations is about 1e-7 m, so the loop converges as expected for realistic bends (the contraction factor is tiny: lever arm 0.2 m times a rotation error of a few mrad).
- With angular noise per aligner call of 0.1 and 0.5 degrees, 2 iterations still passed (change below 1 mm), but a noisier or non-deterministic aligner (RANSAC in `model_aligner`) with more than about 0.3 degrees of jitter on a 0.5 m arm would never get under 1 mm and raise after 5 iterations. Tolerance is a fixed 1 mm; consider making the stop rule relative to the expected rotation noise, or return the last priors with the final change instead of discarding them on non-convergence.
- It returns priors and an iteration count but not the final aligned rotations or last change, which slice 6 will want for the QA report. Return a small result record (priors, iterations, last_change_m).
- Iteration 1 always compares against raw antenna positions, so the minimum possible count is 2 whenever the lever arm exceeds 1 mm. The test bound `iterations <= 3` is fine; the spec's "one or two" is accurate.

### S8. The synthetic aligner is a fair imitation but narrow

It is not circular in the bad sense: it fits a real Umeyama similarity from the true model camera centres (in an arbitrary scaled, rotated frame) to the priors it is given, then returns the true rotations pushed through that fit, so the lever-arm bias propagates into the rotation exactly as `model_aligner` would. What it does not have: noise in antenna positions or the reconstruction, drift, outlier rejection (`model_aligner` is robust), a camera extrinsic error versus the nominal config, an exactly straight ride (B1), and few frames. I ran a few of these ad hoc: 2 degrees of per-camera extrinsic error gives 0.2 mm worst error; 10-frame bend gives 0.16 mm; so convergence is robust for bends. Add the straight ride and a nominal-extrinsic-error case as tests.

### S9. Spec deviations to record

- Plan says "antenna height above the road at rest (QA only)"; the file has `[reference] height_above_road_m`, the reference point height, which is the quantity slice 7 needs. Reasonable, but it is loaded and never used, nor printed except in rig-check, and the antenna height is not derivable without a note. Update the plan or the key name.
- Plan says "phase-centre offset (from the data sheet, cited)": not done (see S6).

## Nit

### N1. Geometry (checked, correct), with one gap for slice 6

Verified by code and by the tests plus my own probes:
- `rig_from_camera = Rz(yaw) Rx(pitch) Ry(roll) L` with L mapping camera x to rig x, camera y (down) to rig -z, camera z (forward) to rig y. Level camera looks along +y, right is +x, down is -z. Yaw + turns the lens left (Rz+ takes y towards -x), pitch + tilts up (Rx+ takes y towards +z), roll + (Ry+ about forward, which appears clockwise from behind) takes camera right towards down. This is the intrinsic Z-X-Y order: yaw, then pitch about the yawed x, then roll about the optical axis. Documented meanings match; the tests pin them.
- `camera_centre = a + R_wr (t - l)` and `reference_point = a - R_wr l` are correct (`a = ref + R l`). `world_from_rig = world_from_camera @ rig_from_camera.T` in `refine_priors` is the right composition.
- Mapping to COLMAP: `cam_from_rig_rotation = rig_from_camera(i).T`, translation `= -R^T t_cam_in_rig`. That is clean, but there is no helper, and COLMAP's rig config needs a reference sensor with identity pose, so the rig origin (reference point on the bar) is not the COLMAP rig origin unless re-expressed in the reference camera's frame (the handoff text says "in the rig reference-camera frame"). Slice 6 should add `cam_from_rig(index)` and a reference-camera re-expression, with a test that `cam_from_rig` composed with a world pose reproduces `camera_centre`. Note also the rotation order is Euler-extrinsic-sensitive: yaw 55 with pitch 0 in the example is safe; large pitch with yaw would not match a naive "pitch up from the horizon" reading, so keep angles small or state the order in the file header (it currently only says yaw/pitch/roll meanings).

### N2. Config hash covers raw bytes

Comment or whitespace edits change the hash. Acceptable and traceable (it is the file), but say so in the docstring, since "same rig" semantic equality is not what the hash tests.

### N3. Example file accuracy

Matches the handoff layout (two forward cameras with baseline, one up and one down; two side cameras angled outwards, yaw +/-55 meaning 35 degrees forward of lateral, inside the 30-45 forward range). Index to role assignment is a guess; the handoff says "ask Olly", and the file's header says placeholder, which is adequate. Header comment "PLACEHOLDER GEOMETRY" and `measured = false` are consistent. cam0 "forward-left, tilted up" at x -0.15 and cam1 "forward-right, tilted down" is arbitrary.

### N4. Style

- `_check_unique` builds the message with a conditional expression to vary wording ("used twice" versus "appears twice"); two labelled messages are simpler.
- `_rx/_ry/_rz` are re-implemented in `tests/test_rig.py` (`_rot_z`, `_rot_y`); import from rig or a shared fixture.
- `_KEYS[""]` as the top-level key is slightly odd; fine.
- `Rig.camera()` raises `StopIteration` for an unknown index; give it a `KeyError`/`RigError`.
- `photo_sources` returns a tuple used by splatting into `match_photos`, and returns serials as `None` without a rig; fine, but name the return or put it in a one-line docstring example. Comments are otherwise minimal, as asked.

## What was checked and found correct

- rig-check output (arm = camera position minus lever arm, and look direction) matches the formulas.
- Curtain-travel precedence: explicit `--curtain-travel-ms` (including 0) overrides the rig; rig value used if flag absent; 0 without a rig. Correct and `images.json` records the effective value.
- images.json `rig` provenance has name, sha256, measured. Report/CLI do not warn when an unmeasured rig is used in ingest; consider a one-line stderr note, since `measured=false` currently only shows up in the file.
- Cameras absent from the rig get no folder; a session with pulses for them refuses with "no photo folder" instead of silently dropping, which is the safe behaviour.
