#!/usr/bin/env bash
# One-off setup of a fresh training instance (AWS Deep Learning Base OSS Nvidia Driver GPU AMI, Ubuntu 24.04).
# Installs a pinned training stack under /opt/georoute, then smoke-tests it on the GPU and records versions:
#   - Python 3.12 venv with torch 2.9.1 (CUDA 12.8 wheel, or 13.0 if the AMI only has CUDA 13 compilers)
#   - gsplat from a pinned main commit, compiled for the instance's GPU (L4, 8.9) with only the variants the
#     pipeline uses: 1, 3 and 4 channels (depth, RGB, RGB+depth); 2DGS and 3DGUT off. gsplat's default build
#     covers 23 channel counts and took over 70 minutes for two GPU types on a g6.2xlarge (2026-11-16)
#     without finishing.
#   - Node 22 LTS and @playcanvas/splat-transform
# The compile uses gsplat's JIT path into a persistent build directory, so a session that runs out of time
# leaves the next one to resume where ninja stopped. Finished steps are skipped. Pins match gsplat's own GPU
# CI (Ubuntu 24.04, CUDA 12.9 toolchain, torch 2.9.1). Runs as root under SSM and stops at the first error.
set -euo pipefail

GSPLAT_COMMIT=512d366b67073d77ca099ede742683c165dfc23b
TORCH_VERSION=2.9.1
NODE_VERSION=v22.23.5
SPLAT_TRANSFORM_VERSION=3.11.2
ROOT=/opt/georoute
OWNER=ubuntu

step() { printf '\n=== %s (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }
is_done() { [[ -f $ROOT/.done-$1 ]]; }
mark_done() { touch "$ROOT/.done-$1"; }

step "system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev git ninja-build build-essential curl xz-utils libgl1 libglib2.0-0 >/dev/null
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

step "CUDA toolkit"
# Prefer a 12.x toolkit >= 12.8 (matches the cu128 torch wheel's major version); else CUDA 13 with cu130.
cuda_home=""
for candidate in /usr/local/cuda-12.9 /usr/local/cuda-12.8; do
    [[ -x $candidate/bin/nvcc ]] && { cuda_home=$candidate; torch_index=cu128; break; }
done
if [[ -z $cuda_home ]]; then
    for candidate in /usr/local/cuda-13.*; do
        [[ -x $candidate/bin/nvcc ]] && { cuda_home=$candidate; torch_index=cu130; }
    done
fi
[[ -n $cuda_home ]] || { echo "no CUDA 12.8+ or 13.x toolkit found under /usr/local:"; ls -d /usr/local/cuda*; exit 1; }
"$cuda_home/bin/nvcc" --version | tail -n 1
echo "using $cuda_home with torch wheel index $torch_index"

mkdir -p "$ROOT"
chown "$OWNER:$OWNER" "$ROOT"
# Every job sources this, so gsplat's build parameters never change between sessions (a change makes gsplat
# delete its build directory and start again).
{
    echo "export CUDA_HOME=$cuda_home"
    echo "export PATH=$ROOT/node/bin:$ROOT/venv/bin:$cuda_home/bin:\$PATH"
    echo "export TORCH_CUDA_ARCH_LIST=8.9"
    echo "export NUM_CHANNELS=1,3,4"
    echo "export BUILD_2DGS=0"
    echo "export BUILD_3DGUT=0"
    echo "export MAX_JOBS=8"
    echo "export TORCH_EXTENSIONS_DIR=$ROOT/torch_extensions"
} >"$ROOT/env.sh"
chown "$OWNER:$OWNER" "$ROOT/env.sh"
cat "$ROOT/env.sh"
as_owner() { sudo -H -u "$OWNER" bash -c "source $ROOT/env.sh && $*"; }

step "python environment"
if is_done venv; then echo "already done"; else
    rm -rf "$ROOT/venv"
    as_owner "python3 -m venv $ROOT/venv && pip install -q --upgrade pip wheel setuptools"
    as_owner "pip install -q torch==$TORCH_VERSION --index-url https://download.pytorch.org/whl/$torch_index"
    as_owner "pip install -q numpy ninja jaxtyping rich typing_extensions"
    mark_done venv
fi

step "gsplat source"
if is_done gsplat-source; then echo "already done"; else
    rm -rf "$ROOT/gsplat"
    as_owner "git clone -q https://github.com/nerfstudio-project/gsplat.git $ROOT/gsplat && cd $ROOT/gsplat &&
        git checkout -q $GSPLAT_COMMIT && git submodule update -q --init --recursive"
    as_owner "cd $ROOT/gsplat && BUILD_NO_CUDA=1 pip install -q --no-build-isolation ."
    mark_done gsplat-source
fi

step "gsplat GPU code (compiled once into $ROOT/torch_extensions; resumes if interrupted)"
if is_done gsplat-build; then echo "already done"; else
    as_owner "cd /tmp && python -c 'from gsplat.cuda._backend import _C; assert _C is not None; print(\"compiled\")'"
    mark_done gsplat-build
fi

step "node and splat-transform"
if is_done node; then echo "already done"; else
    rm -rf "$ROOT/node"
    tarball=node-$NODE_VERSION-linux-x64.tar.xz
    as_owner "cd $ROOT && curl -fsSLO https://nodejs.org/dist/$NODE_VERSION/$tarball &&
        curl -fsSL https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt | grep ' $tarball\$' | sha256sum -c - &&
        tar -xJf $tarball && mv node-$NODE_VERSION-linux-x64 node && rm $tarball"
    as_owner "npm install -g --silent @playcanvas/splat-transform@$SPLAT_TRANSFORM_VERSION"
    mark_done node
fi

step "smoke test"
cat >"$ROOT/smoke.py" <<'PYEOF'
import time, torch, gsplat
from gsplat import rasterization
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpu", torch.cuda.get_device_name(0))
print("gsplat", gsplat.__version__)
n = 100_000
means = torch.randn(n, 3, device="cuda") + torch.tensor([0, 0, 5.0], device="cuda")
quats = torch.nn.functional.normalize(torch.randn(n, 4, device="cuda"), dim=-1)
scales = torch.full((n, 3), 0.02, device="cuda", requires_grad=True)
opacities = torch.full((n,), 0.5, device="cuda")
colors = torch.rand(n, 3, device="cuda")
viewmats = torch.eye(4, device="cuda")[None]
Ks = torch.tensor([[[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1]]], device="cuda")
for mode in ("RGB", "RGB+ED"):
    start = time.time()
    for _ in range(10):
        image, alpha, _ = rasterization(means, quats, scales, opacities, colors, viewmats, Ks, 1280, 720,
                                        rasterize_mode="antialiased", render_mode=mode)
        image.mean().backward()
    torch.cuda.synchronize()
    print(mode, "rasterize+backward ok", tuple(image.shape), f"{(time.time() - start) / 10 * 1000:.1f} ms per step")
PYEOF
chown "$OWNER:$OWNER" "$ROOT/smoke.py"
as_owner "cd /tmp && python $ROOT/smoke.py"
as_owner "splat-transform --version"

step "versions"
as_owner "{ echo setup \$(date -u +%FT%TZ); nvidia-smi --query-gpu=name,driver_version --format=csv,noheader;
    nvcc --version | tail -n 1; python --version; pip freeze | grep -i -E '^(torch|gsplat|numpy)';
    echo gsplat commit $GSPLAT_COMMIT; node --version; splat-transform --version; cat $ROOT/env.sh; } | tee $ROOT/VERSIONS.txt"
df -h / | tail -n 1
echo "setup finished $(date -u +%FT%TZ)"
