#!/usr/bin/env bash
# Write a .env for this box: GPU backend, your IDs, the GPU device groups, the models folder.
# Usage: ./scripts/setup-env.sh [MODELS_DIR]
#   KILN_BACKEND=vulkan ./scripts/setup-env.sh   # force a backend (vulkan works on NVIDIA too)
set -euo pipefail
cd "$(dirname "$0")/.."

models_dir="${1:-${MODELS_DIR:-/opt/models}}"
gid_of() { getent group "$1" | cut -d: -f3; }

# ---- which GPU?
backend="${KILN_BACKEND:-}"
if [ -z "$backend" ]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then
    backend=cuda
  else
    backend=vulkan
  fi
fi

cuda_arch=default
if [ "$backend" = cuda ]; then
  nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader | sed 's/^/host gpu: /'
  # Build only for the GPUs in this box: much faster than every architecture.
  cuda_arch=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | tr -d '.' | sort -u | paste -sd';')
  if ! docker info 2>/dev/null | grep -qi 'nvidia'; then
    echo "warning: Docker doesn't list the nvidia runtime. Run ./scripts/install-nvidia-toolkit.sh first." >&2
  fi
  compose_file="docker-compose.yml:compose.nvidia.yml"
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
  -e "s|^MODELS_DIR=.*|MODELS_DIR=${models_dir}|" \
  -e "s|^KILN_UID=.*|KILN_UID=$(id -u)|" \
  -e "s|^KILN_GID=.*|KILN_GID=$(id -g)|" \
  -e "s|^VIDEO_GID=.*|VIDEO_GID=$(gid_of video || echo 44)|" \
  -e "s|^RENDER_GID=.*|RENDER_GID=$(gid_of render || echo 993)|" \
  .env.example > .env

mkdir -p data "${models_dir}"
echo "wrote .env:"
grep -E '^(KILN_BACKEND|COMPOSE_FILE|CUDA_ARCHITECTURES|MODELS_DIR|KILN_UID|KILN_GID|VIDEO_GID|RENDER_GID)=' .env | sed 's/^/  /'
