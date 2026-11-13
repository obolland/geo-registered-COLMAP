# Slice 4 review (b836cbe, b7ee0e4)

Reviewed: trajectory.py, ingest.py, cli.py (ingest), tests/test_trajectory.py, tests/test_ingest.py, tests/conftest.py. Ran the suite (160 passed, 8.1 s wall on this machine, ruff check and format clean) and probed with scripts in the scratchpad (nothing in the repo changed).

Verdict: the maths and timing are correct and the field-test output is sound (431 images, 346 clean, 85 flagged not-fixed, plot matches the walk). No blocker. Three should-fix items, mainly refusal hygiene and an error claim that only holds on ideal data.

## Blockers

None.

## Should-fix

### S1. A refused run leaves the previous run's images.json and track.png in the output folder

Evidence: ran `ingest` successfully into `out`, then re-ran into the same `out` with a truncated pvt.csv (CLI exit 1, "pulse 9 ... outside the PVT track"). Afterwards `out/images.json` still existed, and `report.json` had only the `session` key. The same happens for MatchError, TrajectoryError and the no-fixed-epoch refusal (the latter leaves only report.json, but in a reused folder the stale images.json and track.png survive). A downstream slice that globs `out/images.json` would silently consume data from a different session.
Fix: at the start of `ingest()` remove `images.json` and `track.png` from `out` (or write all outputs to a temp dir and move on success). Also record the refusal reason in `report.json` (for example `report["ingest_error"] = str(e)`) so a MatchError or TrajectoryError leaves a trace, as session errors do today. Add a test: ingest OK, break the session, ingest again, assert images.json is gone and report has the error.

### S2. The "2 Hz bob within 3 mm" claim holds for ideal data only; on the real track Hermite shows no measurable gain over linear

Independent verification of the maths first (all agree with the commit message):
- Basis functions h00 = 2s^3-3s^2+1, h10 = s^3-2s^2+s, h01 = -2s^3+3s^2, h11 = s^3-s^2, tangents scaled by h = t1-t0: correct. Exact at epochs (s=0 or 1) and at the last epoch (searchsorted-1 gives n-1, clipped to n-2, s=1 exactly).
- NED to ECEF: `[e, n, -d] @ R.T` with R columns east, north, up: correct.
- Constant acceleration: exact (cubic reproduced).
- Bend r=20 m, v=8 m/s, h=0.2: I got 0.002 mm worst case over 13 phases (linear 16 mm, matches the comment).
- Bob 25 mm at 2 Hz: worst error 0.8 to 2.3 mm over phases, bound h^4 A w^4/384 = 2.6 mm. Claim holds.
- Precision: GPS seconds near 1.478e9 have ulp 2.4e-7 s, so 0.3 mm at 1.4 m/s and 2 micrometres of position at 8 m/s. Negligible. `t - t0` is formed before dividing by h, so no cancellation inside the basis.

But the real data does not show the benefit. Leave-one-out on field test 1: keep every second epoch (0.4 s spacing), predict the dropped epochs inside all-fixed windows against the measured positions (575 epochs): Hermite error median 27 mm, 95th 58 mm; linear median 24 mm, 95th 49 mm. The comparison is unfair (0.4 s spacing aliases the 2 Hz bob, and reported positions carry about 14 mm hAcc plus 11 mm quantisation at 1e-7 deg), and reported velocity differs from the central position difference by 0.17 m/s rms on a 1.27 m/s walk, which at 0.2 s costs about h/8 x 0.17 = 4 mm. So the claim "3 mm" is the interpolation error only; the real floor is the measurement noise (about 15 to 30 mm), and Hermite's gain at walking pace is below that noise. At cycling speed on bends (where the 16 mm linear error applies) the gain is real, and nothing here is wrong; but the claim needs wording that says so.
Fix: (a) state in the trajectory docstring or STATUS.md that the bounds are interpolation error on noise-free input and that the real prior accuracy is hAcc; (b) add a test that feeds realistic input to the analytic cases: positions rounded to 1e-7 deg (call `np.round` on lat/lon before building PvtTrack) and velocity rounded to 1 mm/s, and assert only that Hermite is no worse than linear plus a stated margin; the current tests would pass with arbitrarily bad behaviour under quantisation. (c) Optional: for the bob case, assert that error stays below the h^4 bound above rather than an arbitrary 3 mm.

### S3. Tests miss several behaviours the plan lists or the code implements

Plan slice 4 list versus tests:
- Week-rollover times: no trajectory or ingest test straddles a week boundary (`T0 = 2444*604800 + 56269.4`, mid-week). Hermite works on continuous GPS seconds so it should pass; add one with T0 = 2445*604800 - 1.0 so the guarantee is pinned at this layer (loading is covered in test_session).
- Float flags: only carrSoln 1 inside a bracket is tested; the `no-3d-fix` branch is untested. I confirmed it works (fixType 2 with carrSoln 2 gives `['no-3d-fix']` on 17 of 18 images), so add the test.
- Not tested: query exactly at the last epoch; `session_frame` (first fixed epoch when the first epochs are float) and its "no RTK-fixed epoch" refusal (I confirmed it exits 1 with only report.json written); `--curtain-travel-ms` through the CLI (the formula is tested, the unit conversion /1000 is not); report written on MatchError; quality interpolation of h_acc/v_acc (`antenna_positions` result `h_acc_m` is never asserted).
Fix: add those cases; each is a few lines using `_track` and the session_dir fixture.

## Nits

- N1. `images.json` omits fields the plan lists or a consumer will want: `kind` (plan says "pulse, kind"; all rows are capture so a constant is cheap), the delay used (`delay_us`), nav period, and the georoute version or git hash for provenance. Per-image `h_acc_m` and `v_acc_m` are interpolated values but there is no statement of whether they are 1-sigma; add one line to the schema docs. Noting: `v_acc_m` serialises as 0.013000000000000001 (float artefact of mm times 1e-3); round at load (`np.round(x*1e-3, 6)`) or divide by 1000.
- N2. `corrAge` and `diff` are not used. In field test 1, 41 of 1160 fixed epochs have corrAge 2-5 s and are not flagged; `diff` is 1 on every row including carrSoln 0, so it carries no information in this data. The handoff does not require either; I would record corrAge (worst of the bracket) in images.json rather than flag it. A carrSoln 2 with diff 0 (which I forced) passes silently, but that combination cannot occur from a real receiver, so no code change needed.
- N3. Exit codes: a `SessionError` raised inside `ingest()` (for example `command_time` on a bad TM2 after matching) maps to exit 2, "session unreadable", although the session loaded fine. Today the integrity check refuses first (exit 1), so it is only reachable by a TM2 problem the session report does not list; either way the message is clear. Also `OSError` from an unwritable `--out` or a missing exiftool surfaces as a traceback; catch it in `_ingest` and exit 1 or 2.
- N4. `ingest()` is about 70 lines doing orchestration, record building, serialisation and summary. Extract `_image_records(captures, t_mid, antenna, enu)` and `_summary(...)`; it makes S1 easier too. Plan says output `ingest/images.json`; the CLI requires `--out` instead (fine, but note it in the plan).
- N5. `antenna_positions` runs a Python loop of `ned_to_ecef` over every epoch and ECEF of the whole track even for a few queries. Fine for 1415 epochs; for a multi-hour route (100k epochs) vectorise by building the rotations in one einsum or only evaluate the epochs in `i` and `i+1`.
- N6. Quality is taken from the bracketing pair even for a time exactly at an epoch, so a photo on a fixed epoch next to a float one is flagged not-fixed. Conservative and arguably right; document it in one line.
- N7. Plot: gap-flagged and no-3d-fix photos are not distinguished (colour is carrSoln only), and the origin (frame anchor) is not marked. The plot is otherwise good and matches the plan (all priors on the track coloured by fix).
- N8. Stub builder (conftest): `datetime.fromtimestamp(taken_s, UTC)` with `microsecond // 10000` truncates, so a float like 1.29 can become .28 (up to 10 ms error, inside the 30 ms matching tolerance); use `round(stamp.microsecond / 10000)` with carry handling or `stamp.replace(microsecond=0)` plus `round(frac*100)`. In test_ingest the FT1 triples use `strptime(...).timestamp()` (local time) but conftest writes UTC, so the stub EXIF differs from the TSV by the local UTC offset outside GMT; intervals still match, but use `.replace(tzinfo=UTC).timestamp()`.
- N9. Runtime: the whole suite is 8.1 s here, not 20 s. Slowest are `test_field_test_1_stub_photos_all_match` (3.4 s) and `test_field_test_1_positions_follow_the_walk` (2.4 s), both building 431 stubs in one exiftool run already (that run is the right approach). Cheaper still: make a session-scoped fixture that builds the 431 FT1 stubs once and share it between test_photos and test_ingest (saves about 2.5 s), and the 18-photo tests could monkeypatch `read_photos` instead of using exiftool at all except for one smoke test. Not worth more than that.
- N10. Style: comments are minimal and docstrings short. `test_ingest_writes_one_record_per_capture_photo` recomputes the expected t_mid with the same formula as the code; it checks wiring, not correctness, which is fine given the unit test with hand numbers (`100.0975`).

## Checks that came out clean

- Timing: `t_mid = T_cmd + delay*1e-6 + E/2 - Tc/2` matches handoff section 3 step 5; Tc defaults 0; the CLI converts ms to s. TM2 time is GPS (timeBase 1 and timeValid enforced by `command_time`). PVT times come from `utc + nano + 18 s` (not iTOW) and are cross-checked against iTOW within 1 ms in session loading, with strictly increasing times enforced, so a wrong-instant error needs both to be wrong consistently. Warm-up pulses never reach `exposure_mid`.
- Quality: worst carrSoln and fixType of the bracket, linear interpolation of hAcc/vAcc, gap flag at h > 0.3 s (the real spacing is 0.20000005 s so float32 jitter is no issue), outside-track refuses (and the session report already pre-flags it as an integrity error). The 85 flagged photos in the field output are 84 float plus 1 carrSoln 0, all hAcc-consistent (carrSoln 0 epochs have hAcc 76 to 134 mm).
- Ingest: frame anchored at the first RTK-fixed epoch and stored in images.json with origin ECEF; report written before the integrity refusal; warm-ups matched then dropped (summary says 1); exit codes 0/1/2 as documented; plot renders correctly.
