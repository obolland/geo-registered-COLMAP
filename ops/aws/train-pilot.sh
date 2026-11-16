#!/usr/bin/env bash
# One pilot gsplat training run on the persistent training instance, through
# ops/aws/grab-and-run.sh / ops/aws/run-on-gpu.sh. It is written, not run here.
#
# The block is pulled from s3://$BUCKET/blocks/$BLOCK/, trained with gsplat's MCMC preset
# (cap_max 1,000,000, 30k steps, SH 3, antialiased, data factor 4), and every result is
# uploaded under s3://$BUCKET/pilot/$BLOCK/$RUN_ID/ even if training or evaluation fails.
# Checkpoints and PLY files are written every 5k steps so a cap or a late failure does not
# lose the run. The hard external cap is 240 minutes; training gets 195 minutes and the rest
# is kept for EVAL/export/upload.
set -euo pipefail

BUCKET=${BUCKET:-georoute-splat-data-086341361260}
BLOCK=${BLOCK:-s160-l100}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
S3_BLOCK=${S3_BLOCK:-blocks/$BLOCK}
S3_RESULTS=s3://$BUCKET/pilot/$BLOCK/$RUN_ID
ROOT=/opt/georoute
WORK=${WORK:-$ROOT/pilot}
DATA=$WORK/data
RUN_DIR=$WORK/runs/$RUN_ID
RESULTS=$RUN_DIR/results
LOG=$RUN_DIR/train.log
TRAIN_TIMEOUT_MIN=${TRAIN_TIMEOUT_MIN:-195}
MAX_MINUTES=${MAX_MINUTES:-240}
GSPLAT_COMMIT=${GSPLAT_COMMIT:-512d366b67073d77ca099ede742683c165dfc23b}
OWNER=ubuntu

log() { printf '\n=== %s (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }

upload_results() {
    status=$?
    set +e
    log "uploading results (job status $status)"
    mkdir -p "$RESULTS"
    if [[ -f $LOG ]]; then cp "$LOG" "$RESULTS/train.log"; fi
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
[[ $RUN_ID =~ ^[A-Za-z0-9._-]+$ ]] || { echo "RUN_ID must be a plain name, got $RUN_ID" >&2; exit 2; }
[[ $TRAIN_TIMEOUT_MIN =~ ^[0-9]+$ ]] && ((TRAIN_TIMEOUT_MIN > 0 && TRAIN_TIMEOUT_MIN < MAX_MINUTES)) || {
    echo "TRAIN_TIMEOUT_MIN must be a positive integer below MAX_MINUTES ($MAX_MINUTES), got $TRAIN_TIMEOUT_MIN" >&2
    exit 2
}
[[ -f $ROOT/env.sh ]] || { echo "missing $ROOT/env.sh; this is not the training instance" >&2; exit 2; }
# As the owner: git refuses a repository owned by another user ("dubious ownership") when run as root.
actual_commit=$(sudo -H -u "$OWNER" git -C "$ROOT/gsplat" rev-parse HEAD)
[[ $actual_commit == "$GSPLAT_COMMIT" ]] || {
    echo "gsplat is at $actual_commit, expected $GSPLAT_COMMIT; refusing to change the JIT build" >&2
    exit 2
}
trap upload_results EXIT

log "space"
mkdir -p "$WORK" "$DATA" "$RUN_DIR" "$RESULTS"
df -Pk "$WORK" | tail -n 1
free_kb=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
((free_kb > 15 * 1024 * 1024)) || { echo "less than 15 GB free under $WORK" >&2; exit 1; }

log "pull block $S3_BLOCK"
aws s3 sync "s3://$BUCKET/$S3_BLOCK/" "$DATA/" --delete --only-show-errors
[[ -f $DATA/manifest.json ]] || { echo "block manifest missing in $DATA" >&2; exit 1; }
[[ -f $DATA/heldout.json ]] || { echo "heldout.json missing in $DATA" >&2; exit 1; }
[[ -d $DATA/images_4 ]] || { echo "images_4 missing in $DATA" >&2; exit 1; }
[[ -d $DATA/sparse/0 ]] || { echo "sparse/0 missing in $DATA" >&2; exit 1; }
chown -R "$OWNER:$OWNER" "$WORK"

as_owner() { sudo -H -u "$OWNER" bash -c "source $ROOT/env.sh && $*"; }

log "checking training dependencies"
if ! as_owner 'python - <<"PY"
import importlib.util, sys
modules = ["torch", "torchvision", "gsplat", "pycolmap", "cv2", "tyro", "torchmetrics",
           "nerfview", "viser", "sklearn", "tensorboard", "imageio", "PIL", "yaml", "matplotlib"]
missing = [name for name in modules if importlib.util.find_spec(name) is None]
print("missing:", missing)
sys.exit(1 if missing else 0)
PY'; then
    log "installing pinned example dependencies"
    # torch is already installed at 2.9.1 by setup-gpu.sh and is pinned so the wheel cannot change.
    # nvidia-ncore, ppisp, fused-bilagrid and fused-ssim are deliberately not installed: this pilot uses
    # COLMAP data, no post-processing, and gsplat falls back to its reference SSIM when fused-ssim is absent.
    as_owner 'pip install -q "torch==2.9.1" "torchvision==0.24.1" "pycolmap==4.2.1" \
        "numpy>=2,<3" scipy scikit-learn tqdm "torchmetrics==1.8.2" \
        "tyro>=0.8.8,!=1.0.9,!=1.0.10" viser "imageio[ffmpeg]" opencv-python-headless \
        Pillow piexif tensorboard pyyaml matplotlib \
        "nerfview @ git+https://github.com/nerfstudio-project/nerfview@4538024fe0d15fd1a0e4d760f3695fc44ca72787"'
    as_owner 'pip check'
fi
as_owner 'python - <<"PY"
import torch, gsplat, pycolmap, torchvision
from gsplat.cuda._backend import _C
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(0))
print("gsplat", gsplat.__version__, "pycolmap", pycolmap.__version__, "torchvision", torchvision.__version__)
assert _C is not None
PY'

as_owner 'python - <<"PY"
import torch
from torchmetrics.image.lpip import LearnedPerceptualImagePatchSimilarity
metric = LearnedPerceptualImagePatchSimilarity(net_type="alex", normalize=True)
image = torch.zeros(1, 3, 32, 32)
print("lpips preflight", float(metric(image, image)))
PY'

log "validate block"
as_owner "python - <<'PY'
import json
from pathlib import Path
import pycolmap
data = Path('$DATA')
heldout = json.loads((data / 'heldout.json').read_text())
train = json.loads((data / 'train.json').read_text())
model = pycolmap.Reconstruction(data / 'sparse' / '0')
names = {image.name for image in model.images.values()}
assert heldout, 'heldout.json is empty'
assert train, 'train.json is empty'
assert not (set(heldout) & set(train)), 'train and held-out overlap'
assert set(heldout) | set(train) == names, 'split does not match the sparse model'
assert all((data / 'images_4' / Path(name).with_suffix('.png')).is_file() for name in train + heldout)
assert {camera.model.name for camera in model.cameras.values()} == {'PINHOLE'}
print('block ok:', len(train), 'train,', len(heldout), 'held-out,', len(model.points3D), 'points')
PY"

log "prepare examples and explicit held-out split"
rm -rf "$WORK/examples"
cp -a "$ROOT/gsplat/examples" "$WORK/examples"
chown -R "$OWNER:$OWNER" "$WORK/examples"
as_owner "python - <<'PY'
from pathlib import Path
path = Path('$WORK/examples/datasets/colmap.py')
text = path.read_text()
old = '''        indices = np.arange(len(self.parser.image_names))
        if split == \"train\":
            self.indices = indices[indices % self.parser.test_every != 0]
        else:
            self.indices = indices[indices % self.parser.test_every == 0]
'''
new = '''        with open(os.path.join(self.parser.data_dir, \"heldout.json\")) as handle:
            heldout = set(json.load(handle))
        names = self.parser.image_names
        missing = heldout - set(names)
        if missing:
            raise ValueError(f'heldout.json names not in the dataset: {sorted(missing)[:3]}')
        val_indices = np.array([i for i, name in enumerate(names) if name in heldout], dtype=int)
        if split == \"train\":
            self.indices = np.array([i for i, name in enumerate(names) if name not in heldout], dtype=int)
        else:
            if len(val_indices) == 0:
                raise ValueError(\"heldout.json selects no images\")
            self.indices = val_indices
'''
if text.count(old) != 1:
    raise SystemExit(f\"heldout split hook found {text.count(old)} times, expected 1\")
path.write_text(text.replace(old, new))
print(\"explicit held-out split patched\")
PY"

as_owner "python - <<'PY'
from pathlib import Path
path = Path('$WORK/examples/simple_trainer.py')
text = path.read_text()
replacements = [
    ('load_exposure: bool = True',
     'load_exposure: bool = False'),
    ('eval_steps: List[int] = field(default_factory=lambda: [7_000, 30_000])',
     'eval_steps: List[int] = field(default_factory=lambda: [10_000, 20_000, 30_000])'),
    ('save_steps: List[int] = field(default_factory=lambda: [7_000, 30_000])',
     'save_steps: List[int] = field(default_factory=lambda: [5_000, 10_000, 15_000, 20_000, 25_000, 30_000])'),
    ('ply_steps: List[int] = field(default_factory=lambda: [7_000, 30_000])',
     'ply_steps: List[int] = field(default_factory=lambda: [5_000, 10_000, 15_000, 20_000, 25_000, 30_000])'),
]
for old_line, new_line in replacements:
    if text.count(old_line) != 1:
        raise SystemExit(f'checkpoint default found {text.count(old_line)} times: {old_line}')
    text = text.replace(old_line, new_line)
path.write_text(text)
print(\"5k checkpoint defaults patched\")
PY"

as_owner "cd $WORK/examples && python - <<'PY'
import simple_trainer
print('example imports ok')
PY"

cat >"$WORK/run-train.sh" <<TRAIN
#!/usr/bin/env bash
set -euo pipefail
source /opt/georoute/env.sh
cd $WORK/examples
timeout --signal=TERM --kill-after=2m "${TRAIN_TIMEOUT_MIN}m" python simple_trainer.py mcmc \
    --data_dir $DATA \
    --result_dir $RESULTS \
    --data_factor 4 \
    --max_steps 30000 \
    --save_ply \
    --disable_viewer \
    --disable_video \
    --antialiased \
    --sh_degree 3 \
    --strategy.cap-max 1000000
TRAIN
chmod +x "$WORK/run-train.sh"
chown -R "$OWNER:$OWNER" "$WORK"

log "training (timeout ${TRAIN_TIMEOUT_MIN} min)"
set +e
sudo -H -u "$OWNER" bash "$WORK/run-train.sh" 2>&1 | tee "$LOG"
train_status=${PIPESTATUS[0]}
set -e
log "training finished with status $train_status"

# The EXIT trap uploads checkpoints, stats, renders and logs on every path. A nonzero status here means a
# failed or capped run, but the artifacts that exist are still preserved for diagnosis.
exit "$train_status"
