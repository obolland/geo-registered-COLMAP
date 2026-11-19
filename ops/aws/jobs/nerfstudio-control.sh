#!/usr/bin/env bash
# Nerfstudio splatfacto control on the same s160-l100 block and split.
#
# One isolated nerfstudio 1.1.5 environment under /opt/georoute/nerfstudio, installed only
# if missing. The block is pulled read-only, a control dataset with our train/held-out lists
# is assembled in $WORK, then ns-train runs 30k iterations at factor 2, ns-eval measures
# PSNR/SSIM/LPIPS, and ns-export writes the splat PLY. Results are uploaded under
# s3://$BUCKET/pilot/$BLOCK/$RUN_ID/nerfstudio/ on every exit path.
set -euo pipefail

BUCKET=${BUCKET:-georoute-splat-data-086341361260}
BLOCK=${BLOCK:-s160-l100}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
S3_BLOCK=${S3_BLOCK:-blocks/$BLOCK}
S3_RESULTS=s3://$BUCKET/pilot/$BLOCK/$RUN_ID/nerfstudio
ROOT=/opt/georoute
WORK=${WORK:-$ROOT/nerfstudio-work/$RUN_ID}
DATA=$WORK/block
NS_DATA=$WORK/ns-data
RESULTS=$WORK/results
LOG=$WORK/train.log
TRAIN_TIMEOUT_MIN=${TRAIN_TIMEOUT_MIN:-180}
MAX_MINUTES=${MAX_MINUTES:-210}
NS_ROOT=$ROOT/nerfstudio
NS_VENV=$NS_ROOT/venv
OWNER=ubuntu

log() { printf '\n=== %s (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }

upload_results() {
    status=$?
    set +e
    log "uploading results (job status $status)"
    mkdir -p "$RESULTS"
    [[ -f $LOG ]] && cp "$LOG" "$RESULTS/train.log"
    echo "$status" >"$RESULTS/exit_status.txt"
    if aws s3 sync "$RESULTS/" "$S3_RESULTS/" --only-show-errors; then
        echo "results uploaded to $S3_RESULTS"
    else
        echo "RESULTS UPLOAD FAILED: $S3_RESULTS" >&2
        [[ $status == 0 ]] && status=1
    fi
    exit "$status"
}

[[ -n $BLOCK && $BLOCK != */* ]] || { echo "BLOCK must be a plain name" >&2; exit 2; }
[[ $RUN_ID =~ ^[A-Za-z0-9._-]+$ ]] || { echo "RUN_ID must be a plain name" >&2; exit 2; }
[[ $TRAIN_TIMEOUT_MIN =~ ^[0-9]+$ ]] && ((TRAIN_TIMEOUT_MIN > 0 && TRAIN_TIMEOUT_MIN < MAX_MINUTES)) || {
    echo "TRAIN_TIMEOUT_MIN must be below MAX_MINUTES ($MAX_MINUTES)" >&2; exit 2
}
[[ -f $ROOT/env.sh ]] || { echo "missing $ROOT/env.sh; this is not the training instance" >&2; exit 2; }
trap upload_results EXIT

log "space"
# Earlier runs' working folders (block copies, checkpoints, splats) fill the 150 GB disk (88% full on
# 2026-11-18); their results are all in S3 (each job uploads on exit), so remove them. The venvs and the
# compiled CUDA extensions are kept.
du -sh "$ROOT"/nerfstudio-work/* "$ROOT"/pilot/runs 2>/dev/null || true
find "$ROOT/nerfstudio-work" -mindepth 1 -maxdepth 1 -type d ! -name "$RUN_ID" -exec rm -rf {} + 2>/dev/null || true
rm -rf "$ROOT/pilot/runs"
mkdir -p "$WORK" "$DATA" "$NS_DATA" "$RESULTS"
df -Pk "$WORK" | tail -n 1
free_kb=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
((free_kb > 20 * 1024 * 1024)) || { echo "less than 20 GB free under $WORK" >&2; exit 1; }

log "pull block $S3_BLOCK"
aws s3 sync "s3://$BUCKET/$S3_BLOCK/" "$DATA/" --delete --only-show-errors
[[ -f $DATA/train.json && -f $DATA/heldout.json ]] || { echo "split files missing" >&2; exit 1; }
[[ -d $DATA/images && -d $DATA/images_2 && -d $DATA/sparse/0 ]] || {
    echo "images, images_2 and sparse/0 are required" >&2; exit 1;
}

as_owner() { sudo -H -u "$OWNER" bash -c "source $ROOT/env.sh && $*"; }

log "prepare nerfstudio dataset (metric poses, explicit split, factor 2)"
rm -rf "$NS_DATA"
mkdir -p "$NS_DATA/colmap/sparse/0" "$NS_DATA/images" "$NS_DATA/images_2"
"$ROOT/venv/bin/python" - "$DATA" "$NS_DATA" <<'PY'
import json
import sys
from pathlib import Path

import pycolmap
from PIL import Image

resized = 0
data = Path(sys.argv[1])
out = Path(sys.argv[2])
train = set(json.loads((data / "train.json").read_text()))
heldout = set(json.loads((data / "heldout.json").read_text()))
assert not (train & heldout)
assert train | heldout == {im.name for im in pycolmap.Reconstruction(data / "sparse/0").images.values()}
recon = pycolmap.Reconstruction(data / "sparse/0")
for image in recon.images.values():
    name = image.name
    suffix = "_train" if name in train else "_eval" if name in heldout else None
    if suffix is None:
        raise SystemExit(f"image not in either split: {name}")
    path = Path(name)
    renamed = str(path.with_name(path.stem + suffix + path.suffix))
    image.name = renamed
    for source_root, target_root, source_suffix, target_suffix in [
        (data / "images", out / "images", path.suffix, path.suffix),
        (data / "images_2", out / "images_2", ".png", ".JPG"),
    ]:
        source = source_root / path.with_suffix(source_suffix)
        target = target_root / Path(renamed).with_suffix(target_suffix)
        if not source.is_file():
            raise SystemExit(f"missing image: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target_root.name == "images_2":
            # nerfstudio expects floor(size / 2); images_2 was rounded, so an odd 5415 px width gives 2708
            # where nerfstudio wants 2707 (run of 2026-11-18). Resize only the images that differ.
            camera = recon.cameras[image.camera_id]
            expected = (camera.width // 2, camera.height // 2)
            with Image.open(source) as picture:
                if picture.size != expected:
                    picture.convert("RGB").resize(expected, Image.BICUBIC).save(target, format="PNG")
                    resized += 1
                    continue
        target.symlink_to(source.resolve())
recon.write(str(out / "colmap" / "sparse" / "0"))
print("prepared", len(train), "train and", len(heldout), "eval images; names carry _train/_eval;", resized, "images_2 resized to nerfstudio sizes")
PY
chown -R "$OWNER:$OWNER" "$WORK"

log "nerfstudio environment"
if [[ ! -x $NS_VENV/bin/ns-train ]]; then
    # The training instance has no conda: uv provides a managed Python 3.10 for this isolated venv.
    mkdir -p "$NS_ROOT"
    chown -R "$OWNER:$OWNER" "$NS_ROOT"
    as_owner "$ROOT/venv/bin/pip install -q uv==0.9.9" 2>&1 | tee -a "$LOG"
    as_owner "$ROOT/venv/bin/uv venv --python 3.10 --python-preference only-managed --seed $NS_VENV" 2>&1 | tee -a "$LOG"
    as_owner "$NS_VENV/bin/pip install --upgrade pip wheel setuptools" 2>&1 | tee -a "$LOG"
    # torch 2.1.2 is the POC-era API line; cu121 wheels match the 12.x driver on the L4.
    as_owner "$NS_VENV/bin/pip install torch==2.1.2 torchvision==0.16.2 \
        --index-url https://download.pytorch.org/whl/cu121" 2>&1 | tee -a "$LOG"
    as_owner "$NS_VENV/bin/pip install numpy==1.26.4 nerfstudio==1.1.5 \
        gsplat==1.4.0 nerfacc==0.5.2 pymeshlab==2025.7.post1" 2>&1 | tee -a "$LOG"
    as_owner "$NS_VENV/bin/pip check" 2>&1 | tee -a "$LOG"
else
    log "reusing $NS_VENV"
fi
as_owner "$NS_VENV/bin/python -c 'import importlib.metadata, torch, gsplat, nerfstudio, nerfacc; print(\"torch\", torch.__version__, \"cuda\", torch.version.cuda, \"gsplat\", gsplat.__version__, \"nerfstudio\", importlib.metadata.version(\"nerfstudio\"), \"nerfacc\", nerfacc.__version__)'" 2>&1 | tee -a "$LOG"

# torch 2.1's cpp_extension imports pkg_resources, which setuptools removed in recent releases; the
# 2026-11-18 run failed on it. Pin a release that still ships it (idempotent on a reused venv).
as_owner "$NS_VENV/bin/pip install -q setuptools==69.5.1 && $NS_VENV/bin/python -c 'import pkg_resources, torch.utils.cpp_extension; print(\"pkg_resources ok\")'" 2>&1 | tee -a "$LOG"

log "compile CUDA extensions for L4 (sm_89)"
as_owner "export TORCH_CUDA_ARCH_LIST=8.9 TORCH_EXTENSIONS_DIR=$NS_ROOT/torch_extensions MAX_JOBS=8; \
    cd /tmp && $NS_VENV/bin/python - <<'PY'
import torch
from gsplat.cuda._backend import _C
import nerfacc
assert _C is not None
print('gsplat CUDA extension ok; nerfacc ok; gpu', torch.cuda.get_device_name(0))
PY" 2>&1 | tee -a "$LOG"

log "check the trainer and exporter parse"
as_owner "$NS_VENV/bin/ns-train splatfacto --max-num-iterations 30000 \
    --viewer.quit-on-train-completion True --vis tensorboard \
    colmap --data $NS_DATA --downscale-factor 2 --eval-mode filename --help >/dev/null \
    && $NS_VENV/bin/ns-export gaussian-splat --help >/dev/null \
    && echo 'ns-train and ns-export parse'" 2>&1 | tee -a "$LOG"

log "train splatfacto (30k, factor 2, filename train/eval split)"
cat >"$WORK/run.sh" <<TRAIN
#!/usr/bin/env bash
set -euo pipefail
source $ROOT/env.sh
export TORCH_CUDA_ARCH_LIST=8.9
export TORCH_EXTENSIONS_DIR=$NS_ROOT/torch_extensions
export MAX_JOBS=8
export CUDA_VISIBLE_DEVICES=0
cd $RESULTS
timeout --signal=TERM --kill-after=2m "${TRAIN_TIMEOUT_MIN}m" $NS_VENV/bin/ns-train splatfacto \
    --max-num-iterations 30000 \
    --viewer.quit-on-train-completion True \
    --vis tensorboard \
    colmap \
    --data $NS_DATA \
    --downscale-factor 2 \
    --eval-mode filename
TRAIN
chown "$OWNER:$OWNER" "$WORK/run.sh"
set +e
sudo -H -u "$OWNER" bash "$WORK/run.sh" 2>&1 | tee -a "$LOG"
train_status=${PIPESTATUS[0]}
set -e
log "training finished with status $train_status"
((train_status == 0)) || { echo "training did not complete cleanly; artifacts will still upload" >&2; exit "$train_status"; }

CFG=$(find "$RESULTS/outputs" -name config.yml -type f -print | sort | tail -n 1)
[[ -f $CFG ]] || { echo "no config.yml under $RESULTS/outputs" >&2; exit 1; }
cp "$CFG" "$RESULTS/config.yml"

# The config records output_dir relative to the directory ns-train ran in. ns-eval and ns-export must
# run from that same directory or they look for the checkpoint in the wrong place (2026-11-19 run).
log "evaluate on heldout list"
as_owner "cd $RESULTS && $NS_VENV/bin/ns-eval --load-config $CFG --output-path $RESULTS/eval.json" 2>&1 | tee -a "$LOG"

log "export gaussian splat PLY"
as_owner "cd $RESULTS && $NS_VENV/bin/ns-export gaussian-splat --load-config $CFG --output-dir $RESULTS/ply" 2>&1 | tee -a "$LOG"

log "versions and artifacts"
{
    echo "run_id $RUN_ID"
    echo "train_status $train_status"
    echo "config $CFG"
    "$NS_VENV/bin/pip" freeze | grep -i -E '^(torch|torchvision|gsplat|nerfstudio|nerfacc|numpy)'
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
} | tee "$RESULTS/versions.txt"
ls -lh "$RESULTS" "$RESULTS/ply" 2>/dev/null | tee -a "$LOG"
