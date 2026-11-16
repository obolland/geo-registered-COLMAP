#!/usr/bin/env bash
# One-off setup of a fresh training instance (AWS Deep Learning Base OSS Nvidia Driver GPU AMI, Ubuntu 24.04).
# Installs a pinned training stack under /opt/georoute, then smoke-tests it on the GPU and records versions:
#   - Python 3.12 venv with torch 2.9.1 (CUDA 12.8 wheel, or 13.0 if the AMI only has CUDA 13 compilers)
#   - gsplat from a pinned main commit, compiled ahead of time for L4 (8.9) and A10G (8.6) GPUs
#   - Node 22 LTS and @playcanvas/splat-transform
# Pins match gsplat's own GPU CI (Ubuntu 24.04, CUDA 12.9 toolchain, torch 2.9.1). Runs as root under SSM;
# fails at the first error, so a partial install is visible rather than silently accepted.
set -euo pipefail

GSPLAT_COMMIT=512d366b67073d77ca099ede742683c165dfc23b
TORCH_VERSION=2.9.1
NODE_VERSION=v22.23.5
SPLAT_TRANSFORM_VERSION=3.11.2
ROOT=/opt/georoute
OWNER=ubuntu

step() { printf '\n=== %s (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }

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
export CUDA_HOME=$cuda_home PATH=$cuda_home/bin:$PATH

mkdir -p "$ROOT"
chown "$OWNER:$OWNER" "$ROOT"
as_owner() { sudo -H -u "$OWNER" env CUDA_HOME="$CUDA_HOME" PATH="$PATH" bash -c "$*"; }

step "python environment"
as_owner "python3 -m venv $ROOT/venv && $ROOT/venv/bin/pip install -q --upgrade pip wheel setuptools"
as_owner "$ROOT/venv/bin/pip install -q torch==$TORCH_VERSION --index-url https://download.pytorch.org/whl/$torch_index"
as_owner "$ROOT/venv/bin/pip install -q numpy ninja jaxtyping rich typing_extensions"

step "gsplat (compiled ahead of time; takes several minutes)"
as_owner "git clone -q https://github.com/nerfstudio-project/gsplat.git $ROOT/gsplat && cd $ROOT/gsplat &&
    git checkout -q $GSPLAT_COMMIT && git submodule update -q --init --recursive"
as_owner "cd $ROOT/gsplat && TORCH_CUDA_ARCH_LIST='8.6;8.9' MAX_JOBS=6 $ROOT/venv/bin/pip install -q --no-build-isolation ."

step "node and splat-transform"
tarball=node-$NODE_VERSION-linux-x64.tar.xz
as_owner "cd $ROOT && curl -fsSLO https://nodejs.org/dist/$NODE_VERSION/$tarball &&
    curl -fsSL https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt | grep ' $tarball\$' | sha256sum -c - &&
    tar -xJf $tarball && mv node-$NODE_VERSION-linux-x64 node && rm $tarball"
as_owner "PATH=$ROOT/node/bin:\$PATH npm install -g --silent @playcanvas/splat-transform@$SPLAT_TRANSFORM_VERSION"

step "smoke test"
cat >"$ROOT/smoke.py" <<'EOF'
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
start = time.time()
for _ in range(10):
    image, alpha, _ = rasterization(means, quats, scales, opacities, colors, viewmats, Ks, 1280, 720,
                                    rasterize_mode="antialiased")
    image.mean().backward()
torch.cuda.synchronize()
print("rasterize+backward ok", tuple(image.shape), f"{(time.time() - start) / 10 * 1000:.1f} ms per step")
EOF
chown "$OWNER:$OWNER" "$ROOT/smoke.py"
as_owner "$ROOT/venv/bin/python $ROOT/smoke.py"
as_owner "PATH=$ROOT/node/bin:\$PATH splat-transform --version"

step "versions"
as_owner "{ echo setup \$(date -u +%FT%TZ); nvidia-smi --query-gpu=name,driver_version --format=csv,noheader;
    $CUDA_HOME/bin/nvcc --version | tail -n 1; $ROOT/venv/bin/python --version;
    $ROOT/venv/bin/pip freeze | grep -i -E '^(torch|gsplat|numpy)'; echo gsplat commit $GSPLAT_COMMIT;
    $ROOT/node/bin/node --version; PATH=$ROOT/node/bin:\$PATH splat-transform --version; } | tee $ROOT/VERSIONS.txt"
df -h / | tail -n 1
echo "setup finished $(date -u +%FT%TZ)"
