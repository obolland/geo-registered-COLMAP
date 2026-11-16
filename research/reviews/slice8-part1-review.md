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
