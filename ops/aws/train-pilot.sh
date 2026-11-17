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
NEAR_PRUNE_M=${NEAR_PRUNE_M:-1.0}
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
[[ $NEAR_PRUNE_M =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v value="$NEAR_PRUNE_M" 'BEGIN {exit !(value > 0)}' || {
    echo "NEAR_PRUNE_M must be a positive number of metres, got $NEAR_PRUNE_M" >&2
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
cat >"$RESULTS/near-prune.json" <<JSON
{
  "near_prune_m": $NEAR_PRUNE_M,
  "distance": "camera-centre",
  "camera_scope": "train",
  "applies_to": "default subcommand"
}
JSON
log "near-camera prune: $NEAR_PRUNE_M m from training camera centres (centre distance)"
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
           "nerfview", "splines", "viser", "sklearn", "tensorboard", "imageio", "PIL", "yaml", "matplotlib"]
missing = [name for name in modules if importlib.util.find_spec(name) is None]
print("missing:", missing)
sys.exit(1 if missing else 0)
PY'; then
    log "installing pinned example dependencies"
    # torch is already installed at 2.9.1 by setup-gpu.sh and is pinned so the wheel cannot change.
    # nvidia-ncore, ppisp, fused-bilagrid and fused-ssim are deliberately not installed: this pilot uses
    # COLMAP data, no post-processing, and gsplat falls back to its reference SSIM when fused-ssim is absent.
    # Versions resolved and import-tested together locally on 2026-11-17 (simple_trainer imports cleanly;
    # nerfview needs splines, which it does not declare: the first pilot run failed on it).
    as_owner 'pip install -q "torch==2.9.1" "torchvision==0.24.1" "pycolmap==4.2.1" \
        "numpy>=2,<3" "scipy==1.18.1" "scikit-learn==1.9.1" "tqdm==4.70.1" "torchmetrics==1.8.2" \
        "tyro==1.0.17" "viser==1.1.1" "imageio[ffmpeg]==2.38.2" "opencv-python-headless==5.0.0.93" \
        "Pillow==13.0.0" "piexif==1.1.3" "tensorboard==2.21.0" "pyyaml==6.0.3" "matplotlib==3.11.2" \
        "splines==0.3.3" \
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

# The default strategy stops _prune_gs at refine_stop_iter. Patch the copied example with a subclass that
# keeps the prune-only pass running afterwards (no growth, no opacity reset). This is Python in $WORK only;
# the installed gsplat package and its compiled CUDA extension are untouched.
cat >"$WORK/patch-strategy.py" <<'PATCH'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
strategy_old = '''@dataclass
class Config:'''
strategy_new = '''from gsplat.strategy.ops import remove


@dataclass
class PruneAfterRefineStrategy(DefaultStrategy):
    """Keep pruning after refinement, without growing splats.

    The stock strategy returns from step_post_backward at refine_stop_iter, which stops
    _prune_gs as well. This subclass keeps the prune pass running every refine_every.
    Opacity reset is deliberately not continued: it exists to support further
    densification, and with no growth it only creates a transient dip and can leave the
    final model under-opaque.

    It also prunes splats whose centres are within near_prune_m metres of any training
    camera centre, every refine_every steps before and after refine_stop_iter. The
    threshold is expressed in the original world units; configure_near_prune converts it
    to the parser frame by multiplying by the parser transform scale, so it is correct
    with or without normalise_world_space.

    Centre distance is used rather than ellipsoid extent. The failure shell is centred
    0.1-0.5 m from the cameras, so centre distance removes it. A bounding-sphere test
    (centre distance minus the largest scale) would also remove large splats several
    metres away, including useful foreground, and the earlier 0.5 m cull test showed
    that a broad near cull can lower a view's PSNR. The extent test is therefore left out
    until a retrain shows it is needed.
    """

    near_prune_m: float = 1.0
    _near_prune_cameras: torch.Tensor | None = field(default=None, init=False, repr=False, compare=False)
    _near_prune_world_scale: float = field(default=1.0, init=False, repr=False, compare=False)

    def configure_near_prune(self, camera_centres, world_scale: float):
        centres = torch.as_tensor(np.asarray(camera_centres), dtype=torch.float32)
        if centres.ndim != 2 or centres.shape[1] != 3 or len(centres) == 0:
            raise ValueError(f'near-prune camera centres must be Nx3, got {tuple(centres.shape)}')
        scale = float(world_scale)
        if not math.isfinite(scale) or scale <= 0.0:
            raise ValueError(f'near-prune world scale must be positive, got {scale}')
        self._near_prune_cameras = centres
        self._near_prune_world_scale = scale
        print(
            f'Near-camera prune: {len(centres)} training cameras, '
            f'{self.near_prune_m:.3f} m original, world scale {scale:.6f}, '
            f'effective threshold {self.near_prune_m * scale:.6f}'
        )

    @torch.no_grad()
    def _near_prune_mask(self, params):
        if self._near_prune_cameras is None:
            raise RuntimeError('near-camera prune was not configured before training')
        means = params['means']
        cameras = self._near_prune_cameras.to(device=means.device, dtype=means.dtype)
        threshold = float(self.near_prune_m) * float(self._near_prune_world_scale)
        mask = torch.zeros(len(means), device=means.device, dtype=torch.bool)
        for start in range(0, len(means), 8192):
            chunk = means[start : start + 8192]
            distance = torch.cdist(chunk, cameras).amin(dim=1)
            mask[start : start + len(chunk)] = distance <= threshold
        return mask

    def step_post_backward(self, params, optimizers, state, step, info, packed=False, scene=None):
        if step < self.refine_stop_iter:
            super().step_post_backward(
                params, optimizers, state, step, info, packed=packed, scene=scene
            )
        elif step % self.refine_every == 0:
            n_prune = self._prune_gs(params, optimizers, state, step, scene=scene)
            if self.verbose:
                print(
                    f'Step {step}: {n_prune} GSs pruned after refinement. '
                    f'Now having {len(params["means"])} GSs.'
                )

        if step > self.refine_start_iter and step % self.refine_every == 0:
            near = self._near_prune_mask(params)
            n_near = int(near.sum().item())
            if n_near:
                with torch.no_grad():
                    remove(params=params, optimizers=optimizers, state=state, mask=near, scene=scene)
            if self.verbose:
                print(
                    f'Step {step}: {n_near} GSs pruned within '
                    f'{self.near_prune_m * self._near_prune_world_scale:.3f} parser units '
                    f'of a training camera. Now having {len(params["means"])} GSs.'
                )


@dataclass
class Config:'''
if text.count(strategy_old) != 1:
    raise SystemExit(f'strategy class anchor found {text.count(strategy_old)} times, expected 1')
text = text.replace(strategy_old, strategy_new)
use_old = 'strategy=DefaultStrategy(verbose=True),'
use_new = 'strategy=PruneAfterRefineStrategy(verbose=True),'
if text.count(use_old) != 1:
    raise SystemExit(f'default strategy use found {text.count(use_old)} times, expected 1')
text = text.replace(use_old, use_new)
runner_old = '''        self.scene_scale = self.parser.scene_scale * 1.1 * cfg.global_scale
        print("Scene scale:", self.scene_scale)'''
runner_new = '''        self.scene_scale = self.parser.scene_scale * 1.1 * cfg.global_scale
        print("Scene scale:", self.scene_scale)
        if isinstance(self.cfg.strategy, PruneAfterRefineStrategy):
            if not hasattr(self.parser, "camtoworlds") or not hasattr(self.parser, "transform"):
                raise ValueError("near-camera pruning requires a COLMAP parser")
            train_indices = np.asarray(self.trainset.indices, dtype=int)
            camera_centres = self.parser.camtoworlds[train_indices][:, :3, 3]
            world_scale = float(np.cbrt(abs(np.linalg.det(self.parser.transform[:3, :3]))))
            self.cfg.strategy.configure_near_prune(camera_centres, world_scale)'''
if text.count(runner_old) != 1:
    raise SystemExit(f'runner scene-scale anchor found {text.count(runner_old)} times, expected 1')
text = text.replace(runner_old, runner_new)
strategy_field_old = '''    strategy: Union[DefaultStrategy, MCMCStrategy] = field(
        default_factory=DefaultStrategy
    )'''
strategy_field_new = '''    strategy: Union[PruneAfterRefineStrategy, DefaultStrategy, MCMCStrategy] = field(
        default_factory=PruneAfterRefineStrategy
    )'''
if text.count(strategy_field_old) != 1:
    raise SystemExit(f'strategy field anchor found {text.count(strategy_field_old)} times, expected 1')
text = text.replace(strategy_field_old, strategy_field_new)
path.write_text(text)
print('prune-after-refine and near-camera prune strategy patched')
PATCH
chown "$OWNER:$OWNER" "$WORK/patch-strategy.py"
as_owner "python $WORK/patch-strategy.py $WORK/examples/simple_trainer.py"

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
from gsplat.strategy import DefaultStrategy
strategy = simple_trainer.PruneAfterRefineStrategy(verbose=True)
assert isinstance(strategy, DefaultStrategy)
print('example imports ok; prune-after-refine strategy present')
PY"
as_owner "cd $WORK/examples && python simple_trainer.py default --help >/dev/null"

# Training variants, one per line as "name|subcommand|extra arguments". The default is the pilot itself; a
# diagnostic session passes several short variants (MAX_STEPS) to compare settings on the same block. The
# prune-after-refine patch applies to the "default" subcommand; the mcmc preset uses a different strategy.
VARIANTS=${VARIANTS:-"pilot|mcmc|--strategy.cap-max 1000000"}
MAX_STEPS=${MAX_STEPS:-30000}
[[ $MAX_STEPS =~ ^[0-9]+$ ]] || { echo "MAX_STEPS must be an integer, got $MAX_STEPS" >&2; exit 2; }
step_args=""
if ((MAX_STEPS < 30000)); then
    step_args="--eval_steps $MAX_STEPS --save_steps $MAX_STEPS --ply_steps $MAX_STEPS"
fi
variant_count=$(grep -c . <<<"$VARIANTS")
per_variant_min=$((TRAIN_TIMEOUT_MIN / variant_count))
chown -R "$OWNER:$OWNER" "$WORK"
train_status=0
while IFS='|' read -r name subcommand extra; do
    [[ -n $name ]] || continue
    [[ $name =~ ^[A-Za-z0-9._-]+$ ]] || { echo "variant name must be plain, got $name" >&2; exit 2; }
    near_args=""
    if [[ $subcommand == default ]]; then
        near_args="--strategy.near-prune-m $NEAR_PRUNE_M"
    fi
    cat >"$WORK/run-$name.sh" <<TRAIN
#!/usr/bin/env bash
set -euo pipefail
source /opt/georoute/env.sh
cd $WORK/examples
timeout --signal=TERM --kill-after=2m "${per_variant_min}m" python simple_trainer.py $subcommand \
    --data_dir $DATA \
    --result_dir $RESULTS/$name \
    --data_factor 4 \
    --max_steps $MAX_STEPS \
    --save_ply \
    --disable_viewer \
    --disable_video \
    --antialiased \
    --sh_degree 3 \
    $step_args $near_args $extra
TRAIN
    chown "$OWNER:$OWNER" "$WORK/run-$name.sh"
    log "training variant $name: $subcommand $extra ($MAX_STEPS steps, timeout ${per_variant_min} min)"
    set +e
    sudo -H -u "$OWNER" bash "$WORK/run-$name.sh" 2>&1 | tee -a "$LOG"
    status=${PIPESTATUS[0]}
    set -e
    log "variant $name finished with status $status"
    for stats in "$RESULTS/$name"/stats/val_step*.json; do
        [[ -f $stats ]] && echo "$name $(basename "$stats"): $(cat "$stats")"
    done
    ((status == 0)) || train_status=$status
done <<<"$VARIANTS"

# The EXIT trap uploads checkpoints, stats, renders and logs on every path. A nonzero status here means a
# failed or capped variant, but the artifacts that exist are still preserved for diagnosis.
exit "$train_status"
