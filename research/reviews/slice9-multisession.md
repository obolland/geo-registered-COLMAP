# Slice 9: multi-session reconstruction

Status: built 2026-11-19 on branch `slice9`. Steps 1-5 and 7 of `docs/PLAN-MULTISESSION.md`, then
step 6 (the auxiliary iPhone camera). Local only: no AWS resource was started.

## Principle

A single rig and a single pass stay first-class. A single session delegates to the existing
`reconstruct.prepare`, so its workspace, image names, priors and camera assignment are unchanged. Session
prefixes appear only when a workspace holds more than one session. Extra sessions and auxiliary cameras
are optional inputs.

## What is built

- `multisession.py`: a common frame (the first session's, through ECEF, mixed datums refused), session
  naming, `prepare_sessions`, `sessions.json` (frame, source paths, copied rig configs, per-session image
  counts), and helpers to reload the per-session rigs.
- `colmap-prepare` / `colmap-run`: an additional `--session IMAGES_JSON IMAGES_ROOT RIG_TOML` per extra
  session. Several sessions get one COLMAP camera per session and camera folder; image names carry the
  session (`<session>/cam0/IMG_x.JPG`) because Canon file numbers repeat between rides.
- `match`: for a multi-session workspace, `by_session_pair` lists every session pair, including pairs with
  zero verified matches, and `by_session_pair_direction` splits cross-session verified pairs into same and
  opposite travel directions from each image's local camera-centre heading. `by_camera_pair` keys carry
  the session.
- `refine`, `colmap-report`, `rig-calibrate`: each image uses the rig of its session; reports give
  per-session registration, scale, prior and hold-out residuals, rig checks and gate failures;
  `rig-calibrate --session LABEL` calibrates one ride's rig on the joint model.
- `splat-export`: a multi-session block selects every image whose camera centre lies within
  `--image-radius` (20 m default) of the block's path, so overlapping and opposite-direction passes are
  both kept. The held-out rule runs within each session. The manifest records per-session train and
  held-out counts and rigs. Single-session export is unchanged.
- Auxiliary camera (step 6): `ingest --aux-folder cam4 --aux-camera 4 [--aux-clock-offset-s S]
  [--aux-sigma-m 5] [--aux-utc-offset-s T]` orders the phone's photos by EXIF time, converts local EXIF
  time to UTC with OffsetTimeOriginal (or the explicit timezone when the tag is absent), positions each
  from the session's antenna track and gives it a loose prior. Its images carry `auxiliary: true` and
  `pulse: null`; priors.json records the flag only on those images (commit 12d2032), so capture-only
  priors stay byte-identical to pre-slice-9 and readers use `.get`. They map as their own COLMAP camera, are excluded from the hold-out rule and from the
  prior-residual gate, and train in exported blocks. `aux-offset WORKSPACE --images IMAGES_JSON --session
  SESSION_DIR` estimates the clock offset from registered auxiliary images by matching their reconstructed
  camera centres to the nearest antenna track epoch, using `sparse/0` before refinement can pull aux poses
  toward wrong priors. The workflow is: ingest with an approximate offset, map, run `aux-offset`,
  re-ingest with the printed offset, map again.
- `aux-offset` refuses when no auxiliary image has a verified match to a capture image: the initial offset
  must place the priors within roughly 15 m of the track (about 10 s at riding speed). It also refuses a
  p10-p90 spread above 2 s, which means the nearest-epoch fit is ambiguous where the route passes near
  itself.
- A photo outside the track is dropped with a warning naming it, rather than failing the whole ingest; the
  ingest refuses only if none remain. A pulse subset includes auxiliary images whose time falls inside the
  selected pulses' span.
- Older `images.json` documents without receiver velocity (field test 2 session 2 is one) get a velocity
  derived from the antenna track for the rig heading.

## Single-session regression (field test 2 session 1)

A 20-photo subset (pulses 171-175, four cameras) of `~/fieldtest2/out/s1-v120/images.json` with
`rigs/handlebar-4cam-ft2.toml` was prepared twice: by `prepare` from commit `ade298c` (the tree before
slice 9) and by `prepare_sessions` through the new code.

- `priors.json` byte-identical (`cmp` clean).
- Image names identical; camera partition identical (one camera per folder, four cameras, all OPENCV).

Rerun on 2026-11-19 after commit 12d2032 (the auxiliary flag is now written only on auxiliary images):
still byte-identical, with the same names and four-camera partition.

The historical full workspace `~/fieldtest2/colmap/s1` (prepared 2026-11-15, 2464 images) matches the
current image list exactly (0 missing, 0 extra) and the same four-camera folder partition. Its priors
differ from today's by 2.0 cm median: that workspace predates the 16 November rig recalibration, so it is
not the right priors baseline. The old-versus-new comparison above is on identical inputs and is exact.

## Real two-session check

Field test 2 session 1 (pulses 40-125, 344 photos) and session 2 (234 photos) prepared into one workspace:
578 images, 7 cameras (session 1 has four folders; session 2 lost cam1 to its acknowledged fault), one
common ETRS89 frame, each session's rig copied into `workspace/rigs/`.

Matched with `max_neighbors=20, max_distance_m=10` on 2026-11-19 after commit 12d2032: 5362 verified
pairs, 2658 within session 1, 1187 within session 2 and 1517 cross-session. Every cross-session pair is same-direction (0 opposite, 0
unknown) because both rides here ran the same way; opposite directions wait for the test stretch. No image
was left without a verified pair. Cross-session pairs cover the side and forward cameras, for example
session 1 cam2 with session 2 cam0 (247), session 1 cam3 with session 2 cam2 (240) and session 1 cam2 with
session 2 cam2 (206). The common frame is what makes this work: session 2's camera centres lie 0.34 m
(minimum) and 1.34 m (median) from the full session 1 track.

An earlier run of the same slice gave 5365 verified pairs and 1521 cross-session; threaded SIFT matching
moves the count by a few pairs. A first attempt with a non-overlapping slice (session 1 pulses 150-210) produced 0 cross-session pairs
because the two tracks were never closer than 22.8 m; the zero-count session pair appears in
`matching.json` as designed, so a pass that fails to connect is visible rather than looking like a clean
run.

## Review fixes (2026-11-19)

The review's blockers and should-fix items are in. In particular: priors.json records the auxiliary
flag only on auxiliary images from prepare (single and multi), with an end-to-end test that takes the
prepared record through `report` and `export_block`; EXIF OffsetTimeOriginal is parsed and local time converted to UTC, with a
refusal naming the tag when neither the tag nor `--aux-utc-offset-s` is present; `aux-offset` reads
`sparse/0`, matches prefixed multi-session names, refuses when no auxiliary-capture pair verified, and
refuses an ambiguous spread; a single-session route build now filters pulse-less auxiliary images; prepare
clears a stale multi-session overlay (`sessions.json`, `images/`, `rigs/`); and a pulse subset keeps
auxiliary images inside the selected time span.

## Uncertainties

- Cross-direction matching is still the risk the plan names. The real overlapping subset above gives the
  first measurement; the dedicated test stretch decides whether re-aimed cameras and the down-facing iPhone
  carry enough overlap.
- The auxiliary clock-offset estimate is a nearest-epoch fit against the antenna track. A route that
  passes close to itself can bias it; the printed p10/p90 spread makes that visible, and a user-supplied
  approximate offset narrows the search.
- The auxiliary EXIF timezone comes from OffsetTimeOriginal; without it the user must pass the timezone.
  Photos must be JPEG (video frames need extracting first), and the rig must list the auxiliary camera so
  its geometry is known. iPhone HEIC images are not read directly.
- The CUDA side is untouched; no GPU run was made.

## Tests

498 tests pass (`pytest -q`), including the synthetic multi-session prepare, match, report, refine and
export tests and the auxiliary-camera tests.

Real-data commands run locally (no AWS): two prepares of 478 and 578 images and one 3.4 min spatial match.
No mapping was run on the joint model; that belongs to the test-stretch build.
