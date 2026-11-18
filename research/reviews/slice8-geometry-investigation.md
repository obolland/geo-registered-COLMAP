# Investigation: black and white held-out views at pulses 180-210

Date: 2026-11-17. Local analysis only. Read-only downloads from
`s3://georoute-splat-data-086341361260/pilot/s160-l100/20261117T161008Z/metric/` used
`aws s3 cp`; no AWS resource was started or changed.

## Conclusion

The geometry does not disappear. The failing views are occluded by a small number of
enormous splats whose scales were never pruned after step 15,000. The default strategy
returns before pruning at `refine_stop_iter`, so the last 15,000 steps only optimise
parameters. In this run the largest 3D scale grows from 6.98 m at step 14,999 to 29.85 m
at step 29,999. A handful of these splats, and a handful of near-camera splats, cover
whole held-out views with near-black or near-white colour.

The failure is a training-dynamics problem, not a data, split, pose or point-cloud
problem, and not missing geometry. The sharp training-fit edge at pulse 211 is also
training dynamics: the near-camera opaque splat shell ends there (see the seam
investigation below).

## Method

- Checkpoints: `ckpt_{4999,9999,14999,19999,24999,29999}_rank0.pt` from the metric run,
  downloaded read-only to the local scratchpad.
- PLY: `~/fieldtest2/splat/pilot/s160-l100-metric-30k.ply`, and the default-strategy PLY
  `s160-l100-default-30k.ply`.
- Cameras and SfM points: `~/fieldtest2/splat/s160-l100/sparse/0`.
- Pulse mapping: `~/fieldtest2/colmap/s1-gsample/priors.json`.
- Renders: `metric/renders/val_step{9999,19999,29999}_*.png`; the left half is the
  held-out photo and the right half the render.

All projections use the checkpoint's metric frame, `image.cam_from_world()`, the full
resolution K, and the full resolution image bounds. This matches gsplat's parser:
`examples/datasets/colmap.py` converts COLMAP `cam_from_world` to `camtoworlds`, and the
metric run has `normalize_world_space: false`, so no transform is applied.

## Evidence

### 1. Geometry is present near every failing camera

At step 29,999, each failing camera has 14,831 to 18,592 splats within 10 m. Between
1,392 and 5,805 of those project inside the image. This is not an empty region. Examples:

| pulse, camera | splats within 10 m | in image |
|---|---:|---:|
| 180, cam0 | 14,831 | 1,740 |
| 180, cam1 | 14,861 | 5,800 |
| 190, cam3 | 14,786 | 1,392 |
| 200, cam1 | 18,372 | 4,766 |
| 210, cam3 | 16,968 | 1,031 |

The same count at working pulses 280-310 is 18,795 to 35,671 within 10 m and 1,072 to
11,238 in image. The failing views are not under-populated.

### 2. The failure develops after refinement stops

Default strategy defaults at the pinned commit (`gsplat/strategy/default.py:99-107`):

- `prune_opa = 0.005`
- `prune_scale3d = 0.1`
- `prune_scale2d = 0.15`
- `refine_scale2d_stop_iter = 0` (screen-size pruning disabled)
- `refine_stop_iter = 15_000`

`step_post_backward` returns immediately at `step >= refine_stop_iter`
(`gsplat/strategy/default.py:183-184`). Because `_prune_gs` is only called from the
refinement block, both opacity pruning and 3D-scale pruning stop at step 15,000.
`_prune_gs` is at `gsplat/strategy/default.py:343-365`; the 3D-scale test is
`exp(scales).max(dim=-1) > prune_scale3d * scene_scale` at lines 353-355.

The metric run's scene scale is 67.582 (`train.log`, and `examples/datasets/colmap.py`
lines 437-441 computes it as the maximum camera distance from the camera centroid).
The intended 3D-scale cap is therefore 6.758 m.

Measured over all 313,256 splats at step 29,999:

| step | N | max 3D scale (m) | splats > 6.758 m | opaque splats > 6.758 m |
|---:|---:|---:|---:|---:|
| 4,999 | 284,528 | 7.14 | 1 | 1 |
| 9,999 | 243,203 | 6.68 | 0 | 0 |
| 14,999 | 313,256 | 6.98 | 1 | 1 |
| 19,999 | 313,256 | 11.24 | 26 | 23 |
| 24,999 | 313,256 | 18.19 | 44 | 40 |
| 29,999 | 313,256 | 29.85 | 72 | 61 |

The capped behaviour holds through step 14,999, then the tail escapes.

The normalized default-strategy PLY shows the same failure mode: its largest scale is
0.174 against an intended cap of about 0.051 for the 0.51 normalized scene, and 123
opaque splats exceed 0.051. The two runs share the strategy and the data, so this is not a
metric-only artefact.

### 3. Oversized splats cover the failing views

The largest screen-area splats at the failing views, at step 29,999:

| view | render mean | dominant splat |
|---|---:|---|
| p180 cam0 | 0.02 | z = 1.2 m, scale = 0.8 m, opacity = 1.00, RGB = (0.00, 0.01, 0.00) |
| p180 cam3 | 0.10 | z = 0.17 m, scale = 0.68 m, opacity = 0.997, RGB = (0.02, 0.00, 0.00) |
| p190 cam0 | 1.00 | z = 1.2 m, scale = 0.8 m, opacity = 0.67, RGB = (0.90, 0.87, 0.84) |
| p190 cam3 | 1.00 | z = 27.4 m, scale = 9.8 m, opacity = 1.00, RGB = (0.92, 0.90, 0.90) |
| p200 cam1 | 0.98 | z = 0.84 m, scale = 0.8 m, opacity = 0.76, RGB = (0.26, 0.24, 0.19) |

A single splat with scale 9.8 m at 27 m has a projected radius of about 1,539 px on a
1,344 x 881 image; a splat with scale 0.68 m at 0.17 m has a projected radius of about
15,793 px. These are whole-screen covers, not missing geometry.

The exact white views (right half exactly 255,255,255) are covered by white sky-coloured
giants. The dark views are covered by black or dark giants and near-camera splats. The
failure changes with checkpoint and camera:

- p180 cam0: render mean 0.48 at 10k, 0.07 at 20k, 0.02 at 30k.
- p190 cam0: 0.24 at 10k, 1.00 at 20k, 1.00 at 30k.
- p180 cam3: 0.03 at 10k, 0.07 at 20k, 0.10 at 30k.
- working p300 cam0: 0.38, 0.36, 0.36 at 10k, 20k, 30k.
- working p300 cam3: 0.19, 0.21, 0.20 at 10k, 20k, 30k.

The working views are stable because their normal splats occlude the giant tail. The
failing views are those where the tail wins.

### 4. The 180-210 stretch has a dense near-camera point shell

The initial sparse model has no points closer than about 1.2 m to any of these cameras.
The near-camera splats are grown by the optimiser, not initialised there. But the stretch
is much denser in close-range SfM points:

| camera | SfM points within 2 m | splats within 2 m at 30k | opaque nearby at 30k |
|---|---:|---:|---:|
| p180 cam0 | 1,741 | 1,363 | 307 |
| p190 cam0 | 372 | 1,436 | 257 |
| p180 cam3 | 852 | 1,533 | 269 |
| p280 cam0 | 56 | 436 | 190 |
| p300 cam0 | 586 | 436 | 190 |

The near-camera splats at the failing cameras reach maximum scales of up to 4.60 m by
30k, against 0.61-0.79 m at working cameras. The stretch is a narrow walled lane, so the
optimiser has many close surfaces to explain. Once pruning stops, a few of those splats
grow into screen-covering blobs.

### 5. What is ruled out

- Training split: patched parser gives 660 train and 72 val images; val names equal
  `heldout.json` exactly, no overlap.
- Training images: all four cameras present at each pulse; brightness at pulses
  171-211 is in the same range as 285-309. No exposure or blank-image problem.
- Poses: held-out reprojection medians are 1.8-4.8 px at full resolution, in the same
  range as working pulses.
- Baselines: every held-out pulse is bracketed by training pulses 0.69-0.83 m away,
  uniformly across the block. There is no larger gap at 180-210.
- Initial points: closest initial point is 1.19-1.37 m from these cameras; no points
  within 1 m.
- Block edge: pulse 180 is about 7 m from the first training pulse (171) and the export
  has a 25 m overlap margin. The failure is not confined to the first training pulse.
- Rasteriser culling: the GPU projection only culls `z < near_plane` or
  `z > far_plane` (`gsplat/cuda/csrc/ProjectionEWA3DGSFused.cu:100-105`) and
  opacity below `ALPHA_THRESHOLD` (`:172-183`). The oversized splats are opaque and
  inside the depth range, so they are rendered.

The earlier "nearest splat in the frustum is 23-63 m away" observation does not hold for
the metric checkpoint or PLY. With no scale or opacity filter, nearest in-frustum splats
are 0.10-1.2 m away at the failing cameras. The 23-63 m result likely excluded the giant
and near-camera splats, which are exactly the occluders.

## Recommendation

### Cheapest confirmation, no retraining

The proposed filter was tested on CPU, without a GPU session. It removes 1,164 of
313,256 splats (0.37%): 72 giants and 1,092 near-camera large splats, no overlap. The
result is below: it does not recover the failing views. No `splat-cull` tool was added.

### Training fix

The durable fix is to keep pruning active after refinement stops. Preferred small change:
after `refine_stop_iter`, call `_prune_gs` every `refine_every` steps without calling
`_grow_gs`. Do not continue the opacity reset: it exists to support densification, and
with no growth it only creates a transient dip and can leave the final model
under-opaque. Setting `refine_stop_iter = 30000` is a simpler config-only test, but it
also resumes densification and may increase memory and splat count, so it is the second
choice.

Raising `near_plane` is not a suitable fix: it would clip legitimate road and wall
geometry at 1-2 m and would not remove the far white giants.

## CPU cull test (2026-11-17)

The cull was tested without a GPU. The local renderer reproduces the gsplat metric
renders: at half training resolution (675 x 442; camera sizes vary by a few pixels),
the unfiltered CPU render is 36.2 dB median PSNR against the GPU right half, 19.0 dB
minimum, with the failing black and white views matching at 28.6-99 dB. Against the
ground truth, CPU unfiltered scores 19.14 PSNR and 0.616 SSIM, against the GPU run's
19.4 and 0.615; failing views 8.33 against 8.37, the rest 22.23 against 22.55. The
comparison is therefore trustworthy.

Rendering all 72 held-out views for each filter (half training resolution). The filters
are: giant, max 3D scale above 6.758 m; near, distance below 1.5 m to any block camera
centre with max scale above 0.5 m and opacity above 0.5; both is the union.

| filter | removed | all PSNR/SSIM | failing 16 PSNR/SSIM | rest 56 PSNR/SSIM |
|---|---:|---|---|---|
| unfiltered | 0 | 19.14 / 0.616 | 8.33 / 0.402 | 22.23 / 0.677 |
| giant only | 72 | 19.08 / 0.615 | 8.35 / 0.402 | 22.15 / 0.676 |
| near only | 1,092 | 19.43 / 0.625 | 10.60 / 0.449 | 21.95 / 0.675 |
| both | 1,164 | 19.37 / 0.624 | 10.61 / 0.449 | 21.87 / 0.674 |

The giant-only cull changes nothing: p190 cam3 remains an exactly white render and
p180 cam0 remains black; the failing mean stays at 8.35 dB. Removing the near-camera
splats raises the failing mean from 8.33 to 10.60 dB and the failing SSIM from 0.402 to
0.449, but it lowers the rest from 22.23 to 21.95 dB, and the failing views are still
8-20 dB, far from the 22 dB of the rest. The combined cull is identical on the failing
views (10.61 dB) and lowers the rest further to 21.87 dB. The near cull changes some
views from white to grey blur without recovering their PSNR; p180 cam0 stays black at
7.6 dB and p190 cam3 stays exactly white at 3.6 dB. A stronger distance-only cull on
p180 cam0 (remove everything within 1 m of any camera) raises its PSNR from 7.8 to
12.0 dB; within 2 m it becomes a blur at 7.5 dB. The scene behind the near shell is not a correct reconstruction, so a
post-hoc cull cannot recover it. The condition for adding `georoute splat-cull` is not
met.

## Near-camera prune sanity check (2026-11-17, post-hoc)

This is a post-hoc cull of the existing checkpoint, not the training-time prune now
prepared in `ops/aws/train-pilot.sh`. It removes splats whose centres are within a
distance of any training camera centre. All 72 held-out views, half training resolution:

| threshold | removed | all PSNR/SSIM | failing 16 PSNR/SSIM | rest 56 PSNR/SSIM |
|---|---:|---|---|---|
| no removal | 0 | 19.14 / 0.616 | 8.33 / 0.402 | 22.23 / 0.677 |
| 0.25 m | 1,681 | 19.05 / 0.615 | 8.29 / 0.400 | 22.12 / 0.676 |
| 0.50 m | 6,908 | 18.95 / 0.614 | 7.97 / 0.396 | 22.09 / 0.677 |
| 1.00 m | 17,613 | 18.24 / 0.616 | 6.95 / 0.422 | 21.47 / 0.672 |

The 1.0 m centre cull is too aggressive: it lowers both the failing and the rest means.
0.5 m is also slightly negative, and 0.25 m is neutral. The result does not mean the
training-time prune will fail, because it prevents the shell from forming instead of
removing established splats, but it is a warning against assuming that a broad 1.0 m
cull is free. The threshold should be reviewed after the first pruned run.

## Durable training fix (prepared, not run)

Commit `7bd2923` adds a Python-only patch to the copied examples in
`ops/aws/train-pilot.sh`. It defines `PruneAfterRefineStrategy(DefaultStrategy)` and
uses it for the `default` preset. Before `refine_stop_iter` it calls the stock
implementation; afterwards it calls `_prune_gs` every `refine_every` steps without
calling `_grow_gs`. Opacity reset is deliberately not continued: reset exists to
support densification, and with no growth it can only create a transient dip and can
leave the final model under-opaque if the run ends on a reset boundary.

The patch was applied to the exact pinned source and checked locally: the file
compiles, `import simple_trainer` loads, `PruneAfterRefineStrategy` is a
`DefaultStrategy`, a synthetic prune step removes the over-scale and transparent
splats and slices the strategy state, and `python simple_trainer.py default --help`
parses successfully. The patch is in `$WORK/examples/simple_trainer.py`; it does not
touch the installed gsplat package or the compiled CUDA extension, so no JIT rebuild is
triggered. The `mcmc` preset is unchanged, so the patch applies only when the job is
run with the `default` subcommand.

This fix removes the post-15k 3D-scale tail. It does not remove the small 3D-scale,
near-camera shell, because `_prune_gs` has no camera-distance or screen-size rule
enabled by default. If the next run still fails at 180-210, the next lever is a
near-camera or screen-size prune during training, not a larger post-hoc cull.

## Training fit and scene support (2026-11-17)

The same CPU renderer, metric checkpoint 29,999, half resolution. Training views
selected from `train.json`: pulses 171-219 and 281-309, all four cameras. 284 views
were available. The relevant route positions are pulse 171 = s135.5, 180 = 142.5,
190 = 151.0, 200 = 159.0, 210 = 167.0, 215 = 171.0, 219 = 174.0, 281 = 224.0,
290 = 231.5, 300 = 239.5, 309 = 247.0. The nominal block starts at s160 and its image
selection starts at s135.

Training-view fit, half-resolution CPU render against `images_4`:

| stretch | views | cam0 PSNR/SSIM | cam1 PSNR/SSIM | cam2 PSNR/SSIM | cam3 PSNR/SSIM | all PSNR/SSIM |
|---|---:|---|---|---|---|---|
| failing train, 171-219 | 180 | 16.91 / 0.562 | 16.68 / 0.666 | 15.92 / 0.585 | 15.99 / 0.387 | 16.38 / 0.550 |
| working train, 281-309 | 104 | 24.85 / 0.732 | 25.82 / 0.818 | 24.93 / 0.688 | 20.95 / 0.280 | 24.14 / 0.629 |

For comparison, the held-out CPU metrics are 8.33 / 0.402 for the failing views and
22.23 / 0.677 for the working views. The model therefore fails the training views in
the same stretch, not only the held-out views. The renderer's tile cap and
transmittance cutoff were checked against the uncapped render on p180 cam0, p300 cam0
and p190 cam3; PSNR differed by less than 0.01 dB.

The failure is sharp in route distance. Per-pulse training PSNR is 10.9-18.2 dB from
pulse 171 (s135.5) through 209 (s166), then jumps to 22.6 dB at pulse 211 (s168) and
stays 22.6-24.8 dB through 219 (s174). The working stretch is 22.5-25.2 dB throughout.
This is a scene-stretch failure ending around s168, not a gradual loss of context over
tens of metres.

Sparse support in the held-out failing and working views:

| measure | failing 180-210 | working 280-310 |
|---|---:|---:|
| sparse points projecting into view, median depth | 55.0 m | 24.3 m |
| share of projected points within 2 m | 0.05% | 0.15% |
| points actually observed by the image, median depth | 13.3 m | 6.2 m |
| share of observed points within 2 m | 0.3% | 0.0% |
| share of observed points within 5 m | 18.5% | 37.8% |
| nearest same-camera training view baseline | 0.797 m | 0.790 m |
| angle at a point 1.5 m away | 29.8 deg | 29.5 deg |
| angle at the per-view median projected depth | 0.94 deg | 3.0 deg |
| angle at the per-view median observed depth | 5.1 deg | 6.9 deg |

The close foreground seen in the photographs is not well represented by the sparse
points. The failing stretch's projected and observed depths are farther than the
working stretch, and its share of observed points within 5 m is about half. At 1.5 m
the 0.8 m baseline subtends about 30 degrees, which is a lot of parallax, not too
little; the problem is that the sparse points near that depth are scarce.

Ground-truth images inspected at the full `images_4` resolution:

- p180 cam0 (`IMG_4042`) is a close dry-stone wall with a green wooden door,
  a low stone wall on the right, a grass strip, and overcast sky at the top. The wall
  fills almost the whole frame; there is no rider, bike or vehicle, and no visible motion
  blur or exposure problem.
- p190 cam3 (`IMG_8586`) is a foreground grass bank and hedge filling the lower half,
  with a stone house, wall, blue door and trees behind, and overcast sky. No rider, bike
  or vehicle, and no obvious blur.
- p200 cam1 (`IMG_8542`) is an open junction: tarmac, white cottages, a hedge, a bare tree
  and sky. It is not a close-wall view at all.
- Working p300 cam0 (`IMG_4162`) is a close white plaster wall, window and ivy; p300 cam3
  (`IMG_8696`) is a close ivy hedge and road edge. Both are close and high-frequency, and
  the model renders them far better than the failing stretch.

There is no single content type that explains the failure: it includes close stone,
close vegetation and an open junction, and the working stretch also contains close
vegetation. The failing images are all sharp and normally exposed.


## Seam investigation at pulse 210/211 (2026-11-17)

The sharp training-fit edge at pulse 211 was tested as a possible data seam. All tests are
local and read the existing `sparse/refined`, `sparse/0`, `priors.json`, `images.json`,
original EXIF and checkpoint. No AWS resource was used.

### 1. Orientations

Relative rotation of each camera to reference cam2, compared with the calibrated rig
(`rigs/handlebar-4cam-ft2-calibrated.toml`), in degrees:

| stretch | cam0 median/max | cam1 median/max | cam3 median/max |
|---|---:|---:|---:|
| 171-209 | 0.043 / 0.101 | 0.032 / 0.089 | 0.032 / 0.108 |
| 211-240 | 0.044 / 0.116 | 0.033 / 0.097 | 0.039 / 0.085 |
| 281-309 | 0.061 / 0.166 | 0.039 / 0.107 | 0.054 / 0.124 |

The rig is rigid to about 0.03-0.06 deg throughout, with no change at 210/211. The angle
between consecutive absolute rotations is identical across the four cameras to within
0.01 deg, as it must be for a rigid rig. Around the seam, the all-camera step is 2.281 deg
for 208-209, 2.628 deg for 209-210, 3.482 deg for 210-211 and 2.284 deg for 211-212. The
largest local step is 6.122 deg at 204-205. The median is 1.57 deg on the failing stretch
and 1.84-1.89 deg on the working stretch. There is no orientation step, jump or drift
starting or ending at 210.

### 2. Sparse point consistency

Track continuity across consecutive pulses is normal at the seam. The point-set Jaccard
is 0.6119 for 208-209, 0.6226 for 209-210, 0.6212 for 210-211, 0.6146 for 211-212 and
0.6123 for 212-213. Controls are 0.5788 for 281-282 and 0.5521 for 300-301. If 210/211
were a merge seam between two point layers, the cross-seam track count would collapse;
it does not.

For the requested band comparison, A is every point observed by pulses 171-209 and B is
every point observed by pulses 211-240:

| split | A points | B points | observed both | Jaccard |
|---|---:|---:|---:|---:|
| 210/211 | 68,685 | 55,274 | 7,020 | 0.0600 |
| 250/251 control | 68,192 | 58,844 | 6,025 | 0.0498 |
| 281/282 control | 75,233 | 45,375 | 4,403 | 0.0379 |

The seam has at least as many cross-band points as the controls. In a 15 m sphere around
the seam camera, 4,429 points are observed on both sides.

Nearest-neighbour distances between points observed only on one side, inside that 15 m
sphere, are not consistent with duplicated layers offset by cm or dm:

| split | B-only to A-only median / p90 | fraction < 0.1 m | fraction < 0.2 m | within-band median |
|---|---:|---:|---:|---:|
| 210/211 | 0.142 m / 0.390 m | 0.324 | 0.672 | 0.030-0.040 m |
| 250/251 control | 0.167 m / 0.848 m | 0.274 | 0.586 | 0.028-0.033 m |
| 281/282 control | 0.173 m / 0.578 m | 0.268 | 0.569 | 0.028-0.032 m |

The opposite direction (A-only to B-only) is dominated by surfaces seen by the early part
of the route but not by the later part: median 5.56 m at the seam against 3.65 m and
3.25 m at the controls, with only 5.2%, 2.2% and 3.4% below 0.05 m. Its histogram has no
secondary peak at 5-20 cm; the mass increases towards metres. A duplicated layer would
put a local mode at the offset. There is no such mode at the seam, and the seam is not
closer than the controls in either direction.

### 3. Refine sample and reprojection

The 200k sample is the globally longest tracks; the cutoff is track length 9 of 978,646
points. Image observations in each band:

| stretch | sample share of observed points | mean track length | median track length | pre-refine reprojection median | post-refine reprojection median |
|---|---:|---:|---:|---:|---:|
| 171-209 | 0.476 | 15.1 | 9 | 0.815 px | 2.965 px |
| 211-240 | 0.548 | 20.3 | 12.5 | 0.753 px | 2.629 px |
| 281-309 | 0.395 | 12.2 | 7 | 0.867 px | 3.317 px |

The failing stretch is not starved of sample points or long tracks. It has a higher
sample share than the working stretch and only a slightly lower share than the post-seam
stretch. Its pre-refine reprojection is between the other two bands, and its post-refine
reprojection is lower than the working stretch. The points-only adjustment raises the
image-median error everywhere from about 0.8 px to 2.6-3.3 px; that is a separate quality
issue, but it does not single out 171-209 and cannot create a step at 211.

### 4. Image metadata and timing

All four cameras, 99 pulses from 160-230 and 281-309: exposure time 0.001 s, f/8, 18 mm,
white balance 2, AutoISO 100, colour space 1, picture style 132, image size 5184 x 3456,
orientation 1, and the same EF-S18-55mm lens. The serial number and internal serial
number are constant per camera. Focus distance and shutter count are not recorded.

ISO is auto and follows the light. It does not switch at 210/211: cam1 goes 640 at 210
to 800 at 211; cam0 stays 1600 at 211 and becomes 2000 at 212; cam3 stays 1600 at 212
and becomes 2000 at 213; cam2 stays 800 through the seam. File numbers are contiguous
within each range. `DateTimeOriginal` and subsecond time are continuous. In
`images.json`, all 84 images for pulses 200-220 have `flags=[]`, `fix_type=3`,
`bracket_gap_s=0.2`, and correction age 0-2 s. Pulse spacing is 0.82-0.98 s with no gap
at 210/211. A montage of pulses 208-213, all four cameras, shows a continuous village
junction with no scene cut, blank frame or visible exposure change.

### 5. The edge is a near-camera splat boundary

At step 29,999 the checkpoint has a shell of opaque splats within 0.5 m of the training
cameras. It covers the failing stretch and ends at pulse 211:

| stretch | views | views with an opaque splat < 0.5 m | median nearest splat | opaque splats < 1 m per view |
|---|---:|---:|---:|---:|
| 171-209 | 156 | 0.987 | 0.114 m | 97.4 |
| 211-219 | 36 | 0.028 | 0.662 m | 8.4 |

The per-pulse boundary is sharp. Mean opaque splats within 0.5 m are 15.2 at 209, 14.8
at 210, 0.0 at 211, 0.0 at 212, 0.0 at 213 and 0.2 at 214. The nearest splat for all four
cameras is 0.035-0.206 m at 209 and 0.134-0.287 m at 210, then 0.434-0.766 m at 211 and
0.688-0.751 m at 212. The training PSNR jumps from 14.2 dB at 209 to 22.6 dB at 211 in
all four cameras at once.

The sparse model has no points anywhere near this shell: the nearest SfM point is
1.20-1.37 m from every one of these cameras across both stretches (mean 1.264 m on
171-209, 1.225 m on 211-219, zero views below 1 m). The shell is grown by the optimiser,
not initialised from the data.

Sparse support does not improve at the edge, so it cannot explain the fit jump. Mean
per-pulse observed median depth is 15.5 m at 209, 15.9 m at 210, 15.5 m at 211 and 15.1 m
at 212; share of observed points within 5 m is 0.064, 0.062, 0.057 and 0.049. The
aggregate observed median depth is 10.59 m for 171-209 but 14.55 m for 211-219, and the
share within 5 m is 0.248 against 0.072. The post-seam stretch has farther and sparser
sparse support, yet it fits better because the near shell is absent.

The near shell is not the whole failure, and a post-hoc cull does not cleanly recover it.
The earlier "near" filter removes only 1,092 splats because 79% of the close opaque
splats have scale below 0.5 m. Removing everything within 0.5 m raises p180 cam0 from
7.80 to 13.28 dB but lowers p200 cam0 from 13.63 to 10.81 dB, because it also removes
splats that contribute to the foreground. The shell explains the sharp occlusion edge;
the geometry behind it still needs a controlled retrain to assess.

### 6. Verdict

There is no data seam at 210/211. The rig orientation, track continuity, cross-band point
agreement, refine sample, reprojection, EXIF, file numbering and timing are all
continuous. The sharp training-fit edge has a training explanation: a near-camera opaque
splat shell covers essentially every failing training view and stops at pulse 211.

This changes the interpretation of the training-fit table above. The 171-209 PSNR is
largely a measure of how much the near shell occludes the frame, not of whether the
geometry behind it is fitted. The sharp jump at 211 is the edge of that shell, not
evidence that the scene or the capture changes there. The capture-diversity comparison
with the proof-of-concept remains a hypothesis about why the optimiser grows the shell
in this stretch; it is not established by the 211 edge.

## Comparison with the proof-of-concept capture (main session, 2026-11-17)
Copied from the old instance (`ops/aws/jobs/poc-copy.sh`, t3.medium, about 2 billed minutes; local copy `~/fieldtest2/poc-old`): 82 iPhone 15 photos (main camera, 26 mm equivalent, 4032 x 3024, no GPS EXIF) taken over 217 s, one every 3 s, trained with plain `ns-train splatfacto colmap` (defaults) on 2025-12-08. Its COLMAP model (already metric from the GPX alignment) has a camera path of 103 m inside an area only 20.6 x 15.1 m: the same short stretch walked several times, so each part of the road and hedges was seen from several passes, directions and lateral positions, by hand.
Our block: 732 photos along about 150 m from one straight pass at one height (four fixed directions, 0.8 m between pulses). Photos per metre of road are similar (about 4-5); the difference is viewpoint diversity, which near geometry (road, hedges) depends on.

## Conclusion (2026-11-17)

The seam tests rule out a data seam at 210/211. The sharp training-fit edge is the
boundary of an optimiser-grown, near-camera opaque splat shell, not a change in the
capture, poses, points or refine sample. The shell covers essentially every training
view from 171-209 and is absent from 211 onwards. It also means the training-view PSNR
in 171-209 is largely a measure of occlusion, not of whether the scene behind the shell
is fitted. The earlier reading of that table as proof of a scene-stretch failure to fit
is therefore withdrawn.

The held-out failure is still real: 8.33 dB on 180-210 against 22.23 dB on 280-310, and
the model does not deliver usable views in the failing stretch. What the checkpoint
cannot tell us is whether the geometry behind the near shell would fit if the shell were
suppressed. A controlled retrain is needed to separate the two.

The fit jump at 211 cannot be explained by better sparse support either. Observed median
depth per pulse is 15.5 m at 209, 15.9 m at 210, 15.5 m at 211 and 15.1 m at 212, and the
share of observed points within 5 m is 0.064, 0.062, 0.057 and 0.049. The post-seam
aggregate is farther and sparser than the failing aggregate: 14.55 m and 7.2% within 5 m,
against 10.59 m and 24.8%. The fit improves while near-field support gets worse, because
the occluding shell disappears.

The support evidence still makes weak near-field support and limited viewpoint coverage
the leading hypothesis for why the optimiser grows the shell in this stretch. It is a
hypothesis, not a conclusion from the 211 edge. The proof-of-concept capture covered
103 m of path inside a 20.6 x 15.1 m area over several passes, and its 82 photos fitted
the close road and hedges. Our block is one straight pass at one height with four fixed
camera directions, and the failing stretch has a median observed depth of 10.6 m against
6.7 m on the working stretch.

Implications:

- First experiment: a controlled retrain with the prune-after-refine fix plus a
  near-camera or screen-size prune, or depth regularisation, then measure both training
  and held-out fit. This tests whether the geometry behind the shell fits. No paid run
  until Olly approves it.
- Do not treat the 211 edge as evidence for capture diversity. The edge is training
  dynamics. Capture diversity remains a plausible reason the shell forms, but the
  current checkpoint cannot establish it.
- Capture: a second pass or deliberate lateral and height offsets is still a reasonable
  capture experiment for narrow lanes. It is not established as the first fix by this
  investigation.
- Masks: vegetation and hedge masks should reduce ambiguous matches and floaters and are
  worth adding, but they do not address the stone-wall and open-junction views.
- Block margin: a larger margin or discarding the first 10-20 m may keep the shell
  boundary out of the delivered block, but it does not address the cause and may simply
  move the boundary.
- Model capacity and resolution: the fit numbers are contaminated by occlusion, so they
  do not establish underfitting. Re-measure after a valid shell suppression before
  spending on factor-2 or denser splats.
- The prepared prune-after-refine fix removes the post-15k 3D-scale tail, but it does not
  remove the small-scale near-camera shell. Pair it with a near-camera rule or the shell
  will remain.

## Pruned retrain (main session, 2026-11-17 20:45-21:11, about 27 billed minutes)
Default strategy, metric frame, prune-after-refine plus near-camera prune at 0.7 m (Olly approved; 0.7 m chosen over 1.0 m so near hedges survive). Held-out 17.7 / 19.6 / 20.4 PSNR at 10k / 20k / 30k (SSIM 0.597 / 0.627 / 0.647, LPIPS 0.709 / 0.645 / 0.610), 312k splats. Pulses 180-210: median 11.1 dB (min 3.5) against 7.9 unpruned; elsewhere unchanged at 22.6. Partial recovery only: something still occludes or fails there. PLY: ~/fieldtest2/splat/pilot/s160-l100-metric-pruned-30k.ply; results s3://georoute-splat-data-086341361260/pilot/s160-l100/20261117T204542Z/.

## Pruned retrain inspection (2026-11-17)

Read-only inspection of the completed run. The actual metadata is at
`s3://georoute-splat-data-086341361260/pilot/s160-l100/20261117T204542Z/near-prune.json`
(run root, not a `results/` subdirectory). Local copies of all checkpoints, stats, cfg and
the 30k PLY were used. CPU renders are half training resolution, the same renderer and
ground truth as earlier tables.

### Did the prune run

`near-prune.json`: 0.7 m, camera-centre distance, train cameras only. `cfg.yml` confirms
`normalize_world_space: false`, `refine_stop_iter: 15000`, `refine_every: 100`,
`near_prune_m: 0.7`. The log records 294 near-prune events from step 600 to 29900,
removing 9,481 splats: 4,415 before 5k, 3,159 in 5k-10k, 1,793 in 10k-15k, 83 in
15k-20k, 18 in 20k-25k and 13 in 25k-30k. The largest single event removed 148.

Opaque splats within 0.7 m of a training camera at the saved checkpoints:

| checkpoint | total splats | <=0.7 m opaque | 0.7-1 m opaque | 1-2 m opaque | 2-5 m opaque | giants | max scale |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4,999 | 306,682 | 44 | 915 | 11,771 | 27,609 | 1 | 7.08 m |
| 9,999 | 288,903 | 19 | 789 | 5,056 | 20,146 | 3 | 7.41 m |
| 14,999 | 387,447 | 6 | 3,517 | 10,328 | 34,591 | 2 | 8.70 m |
| 19,999 | 362,718 | 2 | 2,651 | 10,305 | 39,700 | 2 | 7.19 m |
| 24,999 | 334,123 | 0 | 2,584 | 10,907 | 45,803 | 2 | 7.33 m |
| 29,999 | 312,197 | 0 | 2,598 | 11,588 | 50,747 | 0 | 6.72 m |
| unpruned 29,999 | 313,256 | 2,275 | 1,657 | 6,669 | 42,120 | 61 | 29.85 m |

The prune ran as intended: the 0.7 m shell is gone at the final checkpoint, the giant tail
is gone, and the largest final scale is inside the 6.758 m cap. Validation PSNR is 17.73 /
19.55 / 20.39. The 72 GPU held-out renders give 13.19 dB mean on the 16 failing views
(median 11.1, min 3.5) and 22.47 dB on the other 56, against 8.33 / 22.23 unpruned.

### What still breaks the failing views

The remaining occlusion is just outside the 0.7 m prune radius. Per-view top projected
splat, pruned checkpoint:

| stretch | views | top area median | views with top area > image | >5 images |
|---|---:|---:|---:|---:|
| train 171-209 | 144 | 1.09 | 75 | 14 |
| train 211-219 | 36 | 0.42 | 0 | 0 |
| train 281-309 | 104 | 0.35 | 2 | 0 |
| held-out 180-210 | 16 | 0.62 | 3 | 3 |

The failing training views still carry 24.8 opaque splats at 0.7-1 m and 393 at 1-2 m on
average; the working stretch has 2.5 and 116. Their top occluder distances are 16 at
0.7-1 m, 57 at 1-2 m, 43 at 2-5 m and 28 beyond 5 m. Typical top occluders are a 3.22 m
scale splat at 1.11 m from p172 cam0, a 2.15 m scale at 0.86 m at p179 cam3, and a 1.75 m
scale at 1.03 m at p187 cam0. A centre-distance rule at 0.7 m cannot catch these.

The held-out cameras were not in the prune set, so they still have close splats. The 16
failing held-out views average 5.6 opaque splats within 0.7 m, nearest 0.067 m. Examples:
p180 cam3 has a 0.74 m scale splat at 0.067 m with projected area 518 times the image;
p190 cam0 has a 0.55 m scale at 0.32 m (13.8 times the image); p190 cam3 has a 0.23 m
scale at 0.13 m (11.8 times). This is why p180/190/200 remain low even though the training
cameras are clear: the held-out camera positions were not protected.

### The 210/211 edge

The old sharp edge at 210/211 is gone. Pruned cam0 training PSNR (mean over available
train pulses, held-out pulses excluded): 171-191 = 16.64 dB against 15.33 unpruned
(+1.32); 192-209 = 20.31 against 14.91 (+5.41); 211-219 = 23.70 against 24.04 (-0.34);
281-309 = 23.94 against 24.86 (-0.92, dominated by p309 cam0 at 18.0 against 24.6). The
seam itself is now p209 22.79 to p211 23.65, a 0.86 dB difference, not the old 8 dB jump.
The remaining deficit is earlier and view-dependent: p172, p174, p179, p187 and p198
are still low in training, while p192-195 and p201-209 mostly recover. The held-out p200
camera remains low for a separate reason, unprotected position. Top projected area tells
the same story: 171-191 median 1.82, 192-209 0.66, 211-219 0.42, working 0.35.

### What helps: scale-aware path prune

A post-hoc ellipsoid test on the pruned checkpoint removes a splat when
`distance_to_nearest_train_camera - max_scale <= 0.7 m` and opacity > 0.5. It removes
3,264 splats. Selected held-out CPU results, no removal -> ellipsoid:

| view | none | ellipsoid | change |
|---|---:|---:|---:|
| p180 cam0 | 7.05 | 13.50 | +6.45 |
| p180 cam3 | 5.98 | 14.14 | +8.16 |
| p200 cam0 | 14.07 | 13.37 | -0.70 |
| p210 cam0 | 22.48 | 22.27 | -0.21 |
| p300 cam0 | 22.94 | 22.52 | -0.42 |
| p300 cam3 | 20.24 | 20.23 | -0.01 |

A path-tube version, using the train camera polylines sampled every 5 cm and the same
ellipsoid offset, removes 3,592 splats. It keeps the p180 gains (13.47 / 14.08) and adds
p190 cam0 15.77 and cam3 16.93, against 8.0 and 9.8 in the GPU baseline. p200 remains
13.38, p210 22.16, p300 22.51 / 20.22. A stricter centre-only 3 m and scale > 1 m cull
removes 133 splats and does not help (p180 unchanged at 7.05 / 3.77, p200 14.06). An
ellipsoid at 0.5 m is weaker (p180 10.45 / 7.45, p200 13.58); at 0.3 m it is ineffective
(p180 5.10 / 3.77). The 0.7 m ellipsoid is the useful threshold.

### Verdict and next step

The near prune worked and removed the 0.7 m shell and the giant tail, but it is too
narrow: the remaining occluders are large splats centred 0.7-2 m away, with 1-3 m scales,
and the held-out cameras were never in the prune set. The most likely next code fix is a
scale-aware path-tube prune: remove a splat when its ellipsoid reaches within 0.7 m of the
continuous training-camera path, not only its centre. This is cheap, uses no held-out
poses, protects interpolated views, and is supported by the ellipsoid and path-tube culls
above.

It will not fully recover the block. p180 improves to 13.5-14.1 dB, still below the 22 dB
working level, and p200 is unchanged by every near cull tried. The early 171-191 stretch
and p197-200 remain underfit behind the shell; they need masks, depth/smoothness
regularisation, or more viewpoint support, not another near-radius increase. A 1.0 m
centre prune was already shown to be harmful post hoc; do not go back to it.

Cheap confirmation before a paid retrain: run the ellipsoid path-tube cull locally over
all 72 held-out views and the failing training views, and if the gain holds, implement the
same rule in `PruneAfterRefineStrategy` and run one 30k diagnostic with 0.7 m path-tube
pruning. No AWS resource was started for this inspection; only read-only `aws s3 cp` was
used.

## Combined retrain preparation (2026-11-17)

Commit `58f1d75` combines three changes. The job environment is
`ops/aws/jobs/pilot-combined-2026-11-17.env`.

### Path-tube prune

The strategy now builds the continuous training-camera path in the Runner from the train
split, one polyline per camera stream, joining consecutive training images only when their
spacing is <= 2 m. On this block that yields 656 segments, length 0.61-2.00 m,
total 597 m across the four streams. The two selected stretches are not joined. The
prune mask removes an opaque splat when

`distance_to_path - max_3d_scale <= NEAR_PRUNE_M * parser_transform_scale`

so the body reaches into the 0.7 m tube. It runs every `refine_every` from step 600 and
after `refine_stop_iter`. Normalisation is handled by the parser transform scale, so it is
correct under both `--normalize_world_space` and `--no-normalize_world_space`.

Local verification: the exact patch applies to pinned gsplat 512d366, `py_compile`
passes, `import simple_trainer` passes, and `simple_trainer.py default` parses
`--data_factor 2 --depth-loss --depth-lambda 0.005 --strategy.near-prune-m 0.7
--no-normalize_world_space --near_plane 0.01 --help`. A synthetic CPU step removes a
0.5 m point from a 10 m path and keeps a 2 m point; normalised case world_scale 2.0
removes 1.3 parser units and keeps 2.0; the before-stop step 10000 also prunes and keeps
the strategy state lengths aligned after `remove`. On the pruned 30k checkpoint the path
mask removes 3,938 opaque splats.

### Cull confirmation

The full-72 CPU held-out pass was started but two working-stretch views exceeded the CPU
render budget; the selected path-cull results are:

| view | no cull | path ellipsoid | source |
|---|---:|---:|---|
| p180 cam0 held-out | 7.05 | 16.82 | full polyline |
| p180 cam3 held-out | 5.98 | 14.08 | sampled tube |
| p190 cam0 held-out | 8.0 | 15.77 | sampled tube |
| p190 cam3 held-out | 9.8 | 16.93 | sampled tube |
| p200 cam1 held-out | 10.9 | 16.07 | full polyline |
| p200 cam0 held-out | 14.07 | 13.38 | sampled tube |
| p300 cam1 held-out | 24.4 | 24.12 | full polyline, fast cap |
| p300 cam0 held-out | 22.94 | 22.52 | sampled tube |
| p300 cam3 held-out | 20.24 | 20.22 | sampled tube |

Training selected cam0, same cull: p171 16.34 -> 17.31; p189 18.59 -> 15.18; p209
22.79 -> 22.44; p211 23.65 -> 23.18. The post-hoc cull is mixed: it strongly helps the
near-camera held-out views but can remove useful near surfaces in already-working training
views. It is not a substitute for the training-time rule, which removes during growth and
lets the model regrow smaller surfaces; it is a warning that a 0.7 m ellipsoid tube may be
too broad in the working stretch. The job keeps the requested 0.7 m rule; if the first
checkpoint shows working-view loss, add a minimum-scale filter or reduce the tube radius.

The full-72 aggregate was therefore not completed. The job decision should use the
training-time result, not this post-hoc table alone.

### Factor-2 images

The block already had `images_4` (1.35 GB, 732 files) but no `images_2`. Generated
locally from the undistorted `images` folder at half size, preserving relative `.png`
names: `~/fieldtest2/splat/s160-l100/images_2`, 732 files, 4.70 GiB (4.81 GB decimal).
It must be uploaded before the run:

`aws s3 sync ~/fieldtest2/splat/s160-l100/images_2 s3://georoute-splat-data-086341361260/blocks/s160-l100/images_2/ --only-show-errors`

Images are about 2688-2701 x 1762-1768, matching the factor-2 parser expectation after
the usual undistort ROI scaling. `train-pilot.sh` now sets `DATA_FACTOR` (default 4),
validates `images_${DATA_FACTOR}`, and passes `--data_factor $DATA_FACTOR`.

### Depth supervision

At pinned gsplat 512d366 the trainer exposes `--depth-loss` and `--depth-lambda`
(default 0.01). With `depth_loss` enabled, `Dataset(load_depths=True)` projects the sparse
COLMAP points for each training image into the image and stores their camera depths;
training samples the rendered expected depth at those pixels and applies
`depth_l1_loss` in disparity space, multiplied by `scene_scale` (67.6 for this metric
block). At p180 cam0 the sparse points give 1,679 in-image depths, median 3.43 m. A 10%
depth error near 3.4 m gives a disparity term of about 1.83 before the weight; at
`depth_lambda 0.005` that is about 0.009 against a photometric loss around 0.2. The job
uses 0.005, half the upstream default, so sparse depth constrains the near surface without
dominating RGB.

### Memory, time and cap

The factor-4 runs logged `torch.cuda.max_memory_allocated()` of 0.88 GB at 30k
(`train_step29999_rank0.json`), not 88% of the L4. Factor 2 has about 4x the pixels per
image; even a 4x increase is under 4 GB on a 24 GB L4. No splat cap is set; monitor the
first checkpoint.

The factor-4 training loop was 1,041 s (17.4 min) for 30k. Factor 2 is expected to take
roughly 4x the rasterisation work, with depth loading and path prunes on top: estimate
60-90 min. The env sets `TRAIN_TIMEOUT_MIN=150`, `MAX_MINUTES=200`, one 30k variant.
That leaves headroom for a slow factor-2 run while preserving checkpoints/results on
timeout.

## Combined retrain (main session, 2026-11-17 22:38 - 2026-11-18 00:04, about 87 billed minutes)
Path-tube body prune at 0.5 m (lowered from 0.7 m so flat road splats about 1.2 m below the cameras survive), data factor 2, depth loss from sparse points (lambda 0.005), prune-after-refine, metric frame. Held-out 20.3 / 22.5 / 23.0 PSNR at 10k / 20k / 30k (SSIM 0.679 / 0.702 / 0.709, LPIPS 0.672 / 0.629 / 0.612), 458k splats. Best so far (previous best 20.4 / 0.647). PLY ~/fieldtest2/splat/pilot/s160-l100-combined-30k.ply (108 MB); results s3://georoute-splat-data-086341361260/pilot/s160-l100/20261117T223802Z/. Per-view and failing-stretch breakdown not yet computed.

## Road visibility and capture recommendation (2026-11-18)

This check uses the refined block poses, the PINHOLE intrinsics and the published route
path transformed from the segment frame into the session ENU frame. It is geometric
visibility, not occlusion-aware, so the counts are upper bounds. The nominal block is
s=160-260, sampled as 101 one-metre road patches on the path centreline.

### Camera geometry

Per camera across the 165 training images in the block:

| camera | height above road median (p5-p95) | pitch median (range) | nearest horizontal road at bottom edge | nearest 3D |
|---|---|---:|---:|---:|
| cam0 | 1.194 m (1.158-1.236) | -0.97 deg (-7.55 to 9.99) | 2.812 m | 3.076 m |
| cam1 | 1.198 m (1.168-1.237) | -1.18 deg (-7.14 to 11.76) | 2.952 m | 3.212 m |
| cam2 | 1.195 m (1.167-1.233) | -1.43 deg (-7.27 to 11.41) | 2.951 m | 3.215 m |
| cam3 | 1.195 m (1.169-1.234) | -0.79 deg (-7.19 to 9.66) | 2.923 m | 3.184 m |

The cameras are level to within about 1.4 deg in the median, with the expected excursions
on turns. The bottom image edge is about 22-23 deg below horizontal, so the road closer
than 2.8-3.0 m horizontally (3.1-3.2 m in 3D) is outside every image. This confirms the
1.18 m / 4300 px / 3530 px estimate.

### What each road patch sees

For each of the 101 patches, count training images whose projection contains the patch,
split by the camera-to-patch distance (the same result as route-s offset because the
patches lie on the path):

| camera-to-patch distance | views per patch, min / median / max | patches with zero |
|---|---:|---:|
| 0-3 m | 0 / 0 / 0 | 101 |
| 3-6 m | 2 / 6 / 9 | 0 |
| 6-10 m | 6 / 10 / 16 | 0 |

Every patch is seen at least 56 times in total (median 193), but no patch is ever seen
from within 3 m. The near-road sections are therefore **never observed at close range**,
not globally unobserved. The distant observations are grazing views, which explains the
blur and floaters where the rider is close to the surface even though the same patch has
many far views.

### Capture options, simulated on the same poses

A downward pitch was applied to all four training cameras (rotation about the camera x
axis), keeping centres and intrinsics. Distance bands use the camera-to-patch distance.
Flat-road approximation: height 1.195 m, vertical half-FOV 22.3 deg, current pitch about
-1 deg.

| added pitch down | road band at bottom edge (approx) | 0-3 m views median / zero patches | 3-6 m median | 6-10 m median |
|---:|---|---:|---:|---:|
| 0 deg (current) | 2.9 m to horizon | 0 / 101 | 6 | 10 |
| 10 deg | 1.9 m to horizon | 2 / 12 | 8 | 10 |
| 15 deg | 1.56 m to horizon | 2 / 9 | 8 | 10 |
| 20 deg | 1.31 m to horizon | 4 / 4 | 8 | 10 |
| 25 deg | 1.09 m to about 27 m | 4 / 0 | 7 | 10 |
| 30 deg | 0.92 m to about 9 m | 4 / 0 | 7 | 8 |
| 45 deg | 0.50 m to about 2.9 m | 4 / 0 | 7 | 2 (band mostly lost) |

The 25-30 deg case is the first that covers every near patch, at the cost of far horizon
coverage. Pitching only one of the four cameras down 20-25 deg would add near views without
losing the forward camera's horizon; pitching all four degrades the far scene.

A dedicated down-facing camera per pulse at 50 deg below horizontal (same intrinsics,
route heading) gives 0-3 m views median 2, max 3, with 1 patch still zero; it gives almost
nothing at 3-6 m and nothing at 6-10 m. One such camera is a near-field filler, not a
standalone capture. Two down cameras (fore/aft) or a wider FOV would remove the remaining
zero patch.

A return pass in the opposite direction does not change the vertical cutoff: 0-3 m stays
zero, while 3-6 and 6-10 m views roughly double (about 12 and 20 median). It helps texture
and cross-track parallax, not the missing near road.

Side cameras at a true 50 deg yaw, kept level, also do not help the near band: the bottom
edge is still 2.9 m away. If they are pitched 50 deg down, they cover a lateral verge strip
around 0.4-2.3 m to the side but not the path centreline (the geometric test gives zero
centreline views). Use a dedicated down camera, not a side yaw, for the road under the
bike.

Recommended capture change, in order:

1. Add one dedicated down camera per pulse at 50-60 deg down, ideally two fore/aft or a
   wider lens, to give every 0-3 m patch 2-4 views.
2. Pitch one existing forward camera down 20-25 deg as a cheaper partial alternative;
   keep the other forward cameras level for the far scene.
3. A return pass is useful for 3-10 m texture and parallax but cannot recover 0-3 m.
4. Do not expect side yaw alone to fix the near road.

## Nerfstudio splatfacto control (prepared, not run)

Script: `ops/aws/jobs/nerfstudio-control.sh`. It is an isolated control, not a change to
the production trainer.

POC config: `~/fieldtest2/poc-old/outputs/unnamed/splatfacto/2025-12-29_214858/config.yml`
records splatfacto, 30,000 iterations, downscale_factor 1, orientation/center none and
auto_scale_poses false. It does not record the package version. The last PyPI nerfstudio
release is 1.1.5 (2024-11-11), whose pins are `gsplat==1.4.0`, `nerfacc==0.5.2`,
`torch>=1.13.1`, `torchvision>=0.14.1`. That is the closest reproducible POC-era pin.

Local Linux x86_64 Python 3.10 resolution (uv, 2026-11-18) succeeded for 250 packages
with: `nerfstudio==1.1.5`, `gsplat==1.4.0`, `nerfacc==0.5.2`, `torch==2.1.2`,
`torchvision==0.16.2`, `numpy==1.26.4`. gsplat 1.4.0 is a `py3-none-any` wheel and
JIT-compiles its CUDA extension on first import; it is not built during pip install. With
the instance toolkit (CUDA 12.9) the script sets `TORCH_CUDA_ARCH_LIST=8.9`. The gsplat
prebuilt-wheel indexes cover other torch/CUDA combinations, not this one. Expect a first
import compile of about 80 s (reported for gsplat 1.4) but allow 5-10 min for gsplat and
nerfacc together. This was resolved locally; the actual CUDA compile cannot be tested on
this Mac.

The script creates `/opt/georoute/nerfstudio/venv` with conda Python 3.10 only if
`ns-train` is absent, installs the pins above, compiles the extensions, then trains from
the same block at factor 2. It builds a control dataset with `images` (for the split
lists), `images_2` with `.JPG` symlinks to our PNGs (nerfstudio's downscale suffix
lookup expects the original `.JPG` names), and `colmap/sparse/0`. `train_list.txt` and
`test_list.txt` are exactly the block train/heldout lists, so the split is identical to
the gsplat runs. The training command is plain splatfacto with 30k iterations,
`--downscale_factor 2`, `--orientation_method none`, `--center_method none`,
`--auto_scale_poses False`, then `ns-eval` on the test list and
`ns-export gaussian-splat` to PLY. Results upload to
`s3://.../pilot/$BLOCK/$RUN_ID/nerfstudio/`.

Factor 2 is a deliberate deviation from the POC's downscale_factor 1: the block has 660
images at up to 5402 px and the gsplat combined run also used factor 2. Factor 1 would be
the closest POC resolution match but is much slower; the job can be changed by setting
`--downscale_factor 1` and providing the full images (they are in the block).

Estimated first-run cost: 10-20 min install/compile, then 60-120 min training at 30k and
factor 2; `TRAIN_TIMEOUT_MIN=180`, `MAX_MINUTES=210`. `ops/aws/run-on-gpu.sh` accepts at
most 240. The job has not been run and no AWS resource was started. `images_2` (4.81 GB)
must be uploaded to the block before either the combined job or the control can pull it.
