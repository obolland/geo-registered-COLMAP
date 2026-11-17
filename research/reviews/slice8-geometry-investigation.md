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
problem, and not missing geometry.

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

Load the existing `ckpt_29999_rank0.pt`, remove:

```python
giant = exp(scales).max(dim=-1).values > 6.758
near = (distance_to_nearest_camera < 1.5) & (exp(scales).max(dim=-1).values > 0.5) & (sigmoid(opacities) > 0.5)
mask = giant | near
```

This removes 1,164 of 313,256 splats (0.37%): 72 giants and 1,092 near-camera large
splats, no overlap. Re-render the 72 held-out views from the filtered checkpoint with
the metric config and no training. Also run the two filters separately, so the effect of
the far giants and the near blobs can be attributed. The eval is about one second of GPU
render work plus checkpoint and scene setup; the paid cost is dominated by starting the
instance, not by the eval. This still needs Olly's approval because it starts the GPU.

If the 16 failing views recover and the overall held-out PSNR rises, the diagnosis is
confirmed and the filtered PLY can be used for the pilot.

### Training fix

The durable fix is to keep pruning active after refinement stops. Preferred small change:
after `refine_stop_iter`, call `_prune_gs` every `refine_every` steps without calling
`_grow_gs`, and keep the opacity reset. That removes over-scale and transparent splats
without restarting densification or growing the splat count. Setting
`refine_stop_iter = 30000` is a simpler config-only test, but it also resumes
densification and may increase memory and splat count, so it is the second choice.

A post-training cull with the same thresholds should be kept regardless, because it is
cheap, observable and protects the export. Raising `near_plane` is not a suitable fix:
it would clip legitimate road and wall geometry at 1-2 m and would not remove the far
white giants.
