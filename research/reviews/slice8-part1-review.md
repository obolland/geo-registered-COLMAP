# Review: slice 8 part 1 (export and pilot job), 2026-11-16

Reviewer: the main session. Code by the DeepSeek peer (commits 4aaaf68, 066185f). Tests 452 pass, ruff clean, no AI trailers, no em dashes.

## Verified
- The real export `~/fieldtest2/splat/s160-l100` (s 160-260 m plus 25 m margins, away from the ivy and parked-vehicle stretches): 732 images (660 train, 72 held out = every 10th pulse), 4 PINHOLE cameras at 5376 x 3525 after undistortion (no downscaling by the undistorter: max_image_size -1), 263,937 points (track >= 3, error <= 2 px), mean reprojection error 0.92 px; images_4 PNGs at factor 4.
- The job's two gsplat patches (explicit held-out split in examples/datasets/colmap.py; 5k checkpoint and PLY steps, exposure loading off in simple_trainer.py) were applied to the pinned commit 512d366 locally: each matched once and both files compile.
- simple_trainer flags used exist at that commit (mcmc subcommand, --antialiased, --save_ply, --disable_viewer, --disable_video, --sh_degree, --data_factor, --max_steps, MCMC cap_max default 1,000,000). The COLMAP loader pairs images/ and images_4/ by sorted relative path and reads point tracks, not image points2D.

## Fixed in review
- Blocker: the job read the gsplat commit with git as root on a repository owned by ubuntu; git refuses ("dubious ownership"), which would have failed the paid run at its start. Now read as the owner (625f14f).

## Follow-ups (not affecting this pilot)
- `pulse_route_s` aligns the camera track to the route by translating with the first pulse; use the exact ECEF transform from the images.json frame instead (block edges are approximate to about a metre today).
- The exported sparse model keeps 112,379 image-to-point references to points dropped by the filter; harmless for gsplat's loader (it reads point tracks) but the model should be made consistent.
- The job pins torch, torchvision, pycolmap, torchmetrics and nerfview but not viser, tensorboard, opencv or the other example dependencies; the import preflight runs before training, so a break costs minutes, not the run. Pin them from the pilot's installed versions afterwards.

## Paid run estimate
Dependencies 5-10 min, S3 pull of 6.4 GB in-region 2-5 min, training 30k MCMC steps on 660 images at 1296 x 864 with up to 1M splats on an L4: about 60-100 min (scaled from typical RTX 4090 timings of 15-25 min, the L4 being about a quarter to a third as fast), evaluation at 10k/20k/30k on 72 images 5 min, upload 5 min. About 1.5-2 h, so about $2-2.50 at $1.24/h; hard cap 4 h (Olly's pre-approval).

## Pilot attempt 1 (2026-11-17 08:11-08:20, about 10 billed minutes)
Failed at the import preflight before training: nerfview (pinned commit) imports `splines` without declaring it. Fixed in 03e353f with every trainer dependency pinned to a set import-tested together locally. Local checks with gsplat's own loader on the real block: 732 images parsed, train 660 / val 72, val equals heldout.json with no overlap, a sample loads at 1350 x 884 with correctly scaled intrinsics (a macOS-only OpenMP threading crash needed OMP_NUM_THREADS=1 locally; not relevant on Linux).

Follow-up: simple_trainer normalises the scene (normalize=True, scene scale 0.51 here), so its PLY is in normalised coordinates; the inverse of the parser's transform must be applied before the splats are placed in the session ENU frame (pilot quality is unaffected).

## Pilot attempt 2 (2026-11-17 09:58-10:38, about 40 billed minutes)
Training ran 20k of 30k steps, then CUDA ran out of memory (16 GB in use at step 20k against 3.4 GB at 5k, with the splat count fixed at 1M). Held-out PSNR 10.1 at 10k and 8.5 at 20k (SSIM 0.46 / 0.33, LPIPS 0.85 / 0.88): renders are a uniform blur. Training loss fell to about 0.11 by step 1.5k, then rose and became unstable (up to about 1.0 by step 15k). Results: s3://georoute-splat-data-086341361260/pilot/s160-l100/20261117T095924Z/.

Ruled out locally: photo/pose pairing (732 of 732 correct), exported pose consistency (recomputed reprojection error median 3.1 px at full resolution, about 0.8 px at training resolution), intrinsics per camera, the held-out split.

Leading hypothesis (unconfirmed): scale. simple_trainer normalises the scene so one unit is about 122 m (transform scale 0.0082); the corridor is about 100 m long but most geometry is 1-5 m from the cameras. Defaults tuned for object-centred scenes (near plane 0.01 units, about 1.2 m here; means learning rate and MCMC noise scaled by scene_scale) are then badly mismatched, consistent with the rising loss, growing splat footprints (memory) and blurred renders.

Runner bug found: run-on-gpu.sh ended the SSM command with `shutdown -c`, so SSM reported Success for the failed job; it now exits with the job's status.

## Diagnostic session (2026-11-17 11:10-11:23, about 15 billed minutes)
Four 3k-step variants on the same block, held-out scores at step 3000: mcmc 19.23 PSNR / 0.637 SSIM / 0.654 LPIPS (851k splats); default 18.26 / 0.616 / 0.680 (392k); mcmc without world normalisation 18.59 / 0.624 / 0.669; default with appearance optimisation 18.91 / 0.630 / 0.662. All train normally to 3k, so the attempt 2 failure develops later.

Attempt 2 checkpoints (CPU inspection): more than half of the 1M splats have opacity near 0 at 5k, 10k and 20k; median largest-axis scale grows from 0.004 to 0.028 to 0.066 units (about 0.5 m, 3.5 m, 8 m at 122 m per unit); the 99th percentile of positions reaches 57 units (about 7 km) at 10k against a camera spread of about 0.8. Diagnosis: MCMC's position noise, which is strongest for low-opacity splats, scatters the many near-transparent splats far outside the scene, and they grow into large blobs (blurred renders, more tile intersections, the memory growth and the OOM). The default (adaptive density control) strategy has no such noise and prunes transparent splats; it is also what splatfacto, which produced a good metric splat in Olly's earlier pipeline, uses.
