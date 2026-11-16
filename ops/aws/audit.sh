#!/usr/bin/env bash
# Read-only audit of the training GPU instance: what is installed and whether it works.
# Changes nothing on the machine. Every check has a time limit and failures are reported, not fatal,
# so one visit gives the whole picture. Runs as root under SSM; checks the default user's environment too.
set -u

section() { printf '\n=== %s ===\n' "$1"; }
run() {
    # run <seconds> <command...>: print the command, its output (trimmed) and exit status
    local limit=$1
    shift
    printf -- '$ %s\n' "$*"
    timeout "$limit" bash -c "$*" 2>&1 | head -n 60
    printf '[exit %s]\n' "${PIPESTATUS[0]}"
}

# The interactive user's home (ubuntu on Ubuntu AMIs, ec2-user on Amazon Linux).
USER_HOME=$(getent passwd ubuntu ec2-user 2>/dev/null | head -n 1 | cut -d: -f6)
USER_NAME=$(getent passwd ubuntu ec2-user 2>/dev/null | head -n 1 | cut -d: -f1)
as_user() { sudo -H -u "${USER_NAME:-root}" bash -lc "$*"; }
export -f as_user
export USER_NAME USER_HOME

section "machine"
run 10 'cat /etc/os-release | head -n 4; uname -r; nproc; free -g | head -n 2; uptime'
run 10 'curl -s -m 2 -H "X-aws-ec2-metadata-token: $(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")" http://169.254.169.254/latest/meta-data/ami-id; echo'
run 10 'cat /etc/motd 2>/dev/null | grep -i -m 3 "deep learning\|AMI"; ls /opt 2>/dev/null'

section "disk"
run 10 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT; df -h / /home /opt 2>/dev/null | sort -u'
run 120 "du -xsh $USER_HOME/* $USER_HOME/.cache $USER_HOME/.conda /opt/conda 2>/dev/null | sort -h | tail -n 15"

section "gpu and cuda"
run 30 'nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv; nvidia-smi | grep -m 1 "CUDA Version"'
run 10 'ls -d /usr/local/cuda* 2>/dev/null; readlink -f /usr/local/cuda; /usr/local/cuda/bin/nvcc --version | tail -n 2'
run 10 'gcc --version | head -n 1'

section "python environments"
run 30 "as_user 'which -a python python3; python3 --version; conda env list 2>/dev/null; ls $USER_HOME/*/bin/activate $USER_HOME/.venv*/bin/activate 2>/dev/null'"
run 60 "as_user 'for p in \$(ls -d /opt/*/bin/python /opt/conda/envs/*/bin/python $USER_HOME/*/bin/python $USER_HOME/.venv*/bin/python $USER_HOME/miniconda3/envs/*/bin/python $USER_HOME/anaconda3/envs/*/bin/python 2>/dev/null; command -v python3); do echo \"## \$p\"; \$p -m pip list 2>/dev/null | grep -i -E \"^(torch|torchvision|gsplat|nerfstudio|numpy|pycolmap|plyfile|tyro|fused|tiny-cuda|opencv-python|imageio|lpips|torchmetrics) \"; done'"

section "torch and gsplat smoke test (each python that has torch)"
cat >/tmp/georoute_gsplat_smoke.py <<'EOF'
import time, torch
print("torch", torch.__version__, "built for cuda", torch.version.cuda, "available", torch.cuda.is_available())
print("device", torch.cuda.get_device_name(0), "capability", torch.cuda.get_device_capability(0))
a = torch.randn(2048, 2048, device="cuda"); torch.cuda.synchronize(); print("matmul ok", float((a @ a).abs().mean()) > 0)
import gsplat
print("gsplat", gsplat.__version__)
from gsplat import rasterization
n = 1000
means = torch.randn(n, 3, device="cuda") + torch.tensor([0, 0, 5.0], device="cuda")
quats = torch.nn.functional.normalize(torch.randn(n, 4, device="cuda"), dim=-1)
scales = torch.full((n, 3), 0.05, device="cuda")
opacities = torch.full((n,), 0.5, device="cuda")
colors = torch.rand(n, 3, device="cuda")
viewmats = torch.eye(4, device="cuda")[None]
Ks = torch.tensor([[[300.0, 0, 160], [0, 300.0, 120], [0, 0, 1]]], device="cuda")
start = time.time()
image, alpha, _ = rasterization(means, quats, scales, opacities, colors, viewmats, Ks, 320, 240,
                                rasterize_mode="antialiased")
torch.cuda.synchronize()
print("rasterization ok", tuple(image.shape), "covered", float(alpha.mean()) > 0, f"{time.time() - start:.1f} s")
EOF
chmod 644 /tmp/georoute_gsplat_smoke.py
run 900 "as_user 'for p in \$(ls -d /opt/*/bin/python /opt/conda/envs/*/bin/python $USER_HOME/*/bin/python $USER_HOME/.venv*/bin/python $USER_HOME/miniconda3/envs/*/bin/python $USER_HOME/anaconda3/envs/*/bin/python 2>/dev/null; command -v python3); do if \$p -c \"import torch\" 2>/dev/null; then echo \"## \$p\"; \$p /tmp/georoute_gsplat_smoke.py 2>&1 | tail -n 8; fi; done'"

section "other tools"
run 20 "as_user 'node --version; npm --version; npm ls -g --depth=0 2>/dev/null | tail -n +2; command -v splat-transform && splat-transform --version'"
run 10 "as_user 'colmap -h 2>/dev/null | head -n 2; command -v ns-train; aws --version'"
run 10 'snap list amazon-ssm-agent 2>/dev/null | tail -n 1; amazon-ssm-agent --version 2>/dev/null'

section "leftovers from earlier work"
run 60 "ls -la $USER_HOME | head -n 40"
run 60 "find $USER_HOME -maxdepth 3 \\( -name 'outputs' -o -name '*.ply' -o -name 'transforms.json' -o -name 'sparse' \\) 2>/dev/null | head -n 20"

section "housekeeping"
run 20 'crontab -l 2>/dev/null; ls /etc/cron.d; systemctl list-timers --no-pager | head -n 12'
run 60 'apt list --upgradable 2>/dev/null | wc -l'
echo
echo "audit finished $(date -u +%FT%TZ)"
