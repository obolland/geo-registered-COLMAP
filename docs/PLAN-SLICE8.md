# Plan: slice 8, splat training (export and pilot first)

Status: proposed 2026-11-16. Inputs: `docs/OUTPUT-DESIGN.md` v0.2 section 6, the refined session 1 reconstruction (`~/fieldtest2/colmap/s1-gsample`), the photos (`~/fieldtest2/s1/cam0..3`), the slice 7 route package (`~/fieldtest2/routes/ft2-s1/20261116T103407Z`), and the training instance set up on 2026-11-16 (`research/STATUS.md`).

## Cost rules (Olly, binding)

GPU time is paid personally. Everything is built and tested locally first; the instance runs only finished, reviewed jobs through `ops/aws/grab-and-run.sh` (one job per visit, hard time cap, always stopped); each paid run is approved with its time and cost estimate beforehand. No trial and error on the instance.

## Step 1: training-set export (local, `georoute splat-export`)

- Select one block by route distance: images whose rig pose lies within the block's `s` range plus an overlap margin (default 25 m each side), all cameras of those pulses.
- Undistort the selected images with COLMAP's undistorter (OPENCV to PINHOLE) and write a PINHOLE sparse model in the session ENU frame, keeping the refined poses.
- Points: the refined model's points inside the block's bounds, filtered as in slice 7 (track length >= 3, error <= 2 px).
- Downscaled copies for training (factor 4 to start: 1296 x 864), full-resolution kept.
- Split: every 10th pulse held out (all its cameras), the same rule as the slice 6 hold-out, written as an explicit list; held-out images never enter training.
- A manifest with the block, the counts, the split, the inputs' hashes and the parameters.
- Output laid out for gsplat's `examples/simple_trainer.py` COLMAP loader (`images/`, `images_4/`, `sparse/0/`), unless research shows our own small trainer wrapper is the more robust choice.

## Step 2: pilot training job (written and reviewed locally, run once)

- One block of about 100 m, chosen away from the known problem stretches (ivy near s = 115 m, parked vehicles near s = 345-380 m).
- gsplat with MCMC (`cap_max` about 1M), antialiased mode, 30k steps, SH degree 3; no masks yet (the chosen block avoids vehicles).
- Metrics on the held-out images: PSNR, SSIM, LPIPS; plus training time, peak GPU memory and splat count.
- Outputs to S3: the trained PLY, metrics, a few held-out renders side by side with the photos, the training log.
- Job script `ops/aws/train-pilot.sh` (data pulled from S3, results pushed back). Time cap and cost estimated from published gsplat timings on an L4-class GPU before the run.

## Later steps (after the pilot is judged)

Masks (vehicles, rider), blocks over the whole route with ownership trimming, appearance matching, floater culling, `splat-transform` streamed SOG, the package's `splats` entries, and a browser preview.

## Done when (this part)

- `splat-export` is tested on synthetic data and produces the pilot block from the real data locally.
- The pilot job script is reviewed, with a time cap and cost estimate, ready for Olly's approval.
