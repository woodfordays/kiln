#!/usr/bin/env bash
# Write a .env for this box: GPU backend, your IDs, the GPU device groups, the models folder.
# Usage: ./scripts/setup-env.sh [MODELS_DIR]
#   KILN_BACKEND=vulkan ./scripts/setup-env.sh   # force a backend (vulkan works on NVIDIA too)
# On Windows, run it from Git Bash with Docker Desktop running.
set -euo pipefail
cd "$(dirname "$0")/.."

models_dir="${1:-${MODELS_DIR:-/opt/models}}"
gid_of() { getent group "$1" 2>/dev/null | cut -d: -f3; }

# Docker Desktop (Windows, macOS) runs containers in a VM with no /dev/dri. On Windows that VM is WSL2
# (a "microsoft" kernel), which reaches the GPU through /dev/dxg: ROCm can use that for AMD cards.
docker_os=$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)
docker_kernel=$(docker info --format '{{.KernelVersion}}' 2>/dev/null || true)
desktop_wsl=0
if [ "$docker_os" = "Docker Desktop" ] && [[ "$docker_kernel" == *microsoft* ]]; then
  desktop_wsl=1
fi

# ROCm's architecture for the Radeon GPUs it supports on WSL, from the name Windows gives the card.
rocm_arch_of() {
  case "$1" in
    *"RX 7900"*|*"PRO W7900"*|*"PRO W7800"*) echo gfx1100 ;;
    *"RX 7800"*|*"PRO W7700"*) echo gfx1101 ;;
    *"890M"*|*"880M"*) echo gfx1150 ;;
    *"8060S"*|*"8050S"*|*"8040S"*) echo gfx1151 ;;
    *"RX 9060"*) echo gfx1200 ;;
    *"RX 9070"*|*"AI PRO R9700"*) echo gfx1201 ;;
  esac
}
rocm_detected=""
if [ "$desktop_wsl" = 1 ]; then
  windows_gpus=$(powershell.exe -NoProfile -NonInteractive -Command '(Get-CimInstance Win32_VideoController).Name' \
    2>/dev/null | tr -d '\r' | sed 's/[[:space:]]*$//' || true)
  while IFS= read -r name; do
    if [ -z "$name" ]; then continue; fi
    echo "host gpu: $name"
    arch=$(rocm_arch_of "$name")
    if [ -n "$arch" ]; then rocm_detected="${rocm_detected:+$rocm_detected;}$arch"; fi
  done <<< "$windows_gpus"
  rocm_detected=$(printf '%s' "$rocm_detected" | tr ';' '\n' | sort -u | paste -sd';' -)
fi

# ---- which GPU?
backend="${KILN_BACKEND:-}"
if [ -z "$backend" ]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    backend=cuda
  elif [ -n "$rocm_detected" ]; then
    backend=rocm
  else
    backend=vulkan
  fi
fi

cuda_arch=default
rocm_arch="${ROCM_ARCHITECTURES:-${rocm_detected:-default}}"
if [ "$backend" = cuda ]; then
  nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader | sed 's/^/host gpu: /'
  # Build only for the GPUs in this box: much faster than every architecture.
  cuda_arch=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d '.' | sort -u | paste -sd';')
  if ! docker info 2>/dev/null | grep -qi 'nvidia'; then
    echo "warning: Docker doesn't list the nvidia runtime. Run ./scripts/install-nvidia-toolkit.sh first." >&2
  fi
  compose_file="docker-compose.yml:compose.nvidia.yml"
elif [ "$backend" = rocm ]; then
  if [ "$desktop_wsl" != 1 ]; then
    echo "error: KILN_BACKEND=rocm is only set up for Docker Desktop on Windows (compose.wsl.yml). On Linux, use vulkan." >&2
    exit 1
  fi
  # The image's librocdxg reaches the GPU through /dev/dxg. Build only for this box's GPU: a faster build, a smaller image.
  echo "Docker Desktop on Windows: ROCm reaches the GPU through /dev/dxg (compose.wsl.yml), built for ${rocm_arch}."
  compose_file="docker-compose.yml:compose.wsl.yml"
elif [ "$docker_os" = "Docker Desktop" ]; then
  # Without /dev/dri the AMD override can't create the container. ROCm on Windows needs a Radeon it supports.
  echo "warning: Docker Desktop has no /dev/dri and no GPU Kiln can reach here: llama.cpp will run on the CPU." >&2
  compose_file="docker-compose.yml"
else
  [ -e /dev/dri ] || echo "warning: /dev/dri not found; the GPU won't be visible in the container" >&2
  command -v vulkaninfo >/dev/null && vulkaninfo --summary 2>/dev/null | grep -E 'deviceName' | sed 's/^/host vulkan: /' || true
  compose_file="docker-compose.yml:compose.amd.yml"
fi

if [ -f .env ]; then
  cp .env .env.bak
  echo "existing .env saved as .env.bak"
fi
sed \
  -e "s|^KILN_BACKEND=.*|KILN_BACKEND=${backend}|" \
  -e "s|^COMPOSE_FILE=.*|COMPOSE_FILE=${compose_file}|" \
  -e "s|^CUDA_ARCHITECTURES=.*|CUDA_ARCHITECTURES=${cuda_arch}|" \
  -e "s|^ROCM_ARCHITECTURES=.*|ROCM_ARCHITECTURES=${rocm_arch}|" \
  -e "s|^MODELS_DIR=.*|MODELS_DIR=${models_dir}|" \
  -e "s|^KILN_UID=.*|KILN_UID=$(id -u)|" \
  -e "s|^KILN_GID=.*|KILN_GID=$(id -g)|" \
  -e "s|^VIDEO_GID=.*|VIDEO_GID=$(gid_of video || echo 44)|" \
  -e "s|^RENDER_GID=.*|RENDER_GID=$(gid_of render || echo 993)|" \
  .env.example > .env

mkdir -p data "${models_dir}"
echo "wrote .env:"
grep -E '^(KILN_BACKEND|COMPOSE_FILE|CUDA_ARCHITECTURES|ROCM_ARCHITECTURES|MODELS_DIR|KILN_UID|KILN_GID|VIDEO_GID|RENDER_GID)=' .env | sed 's/^/  /'
