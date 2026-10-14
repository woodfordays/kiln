#!/usr/bin/env bash
# Write a .env for this box: your IDs, the GPU device groups, and the models folder.
# Usage: ./scripts/setup-env.sh [MODELS_DIR]
set -euo pipefail
cd "$(dirname "$0")/.."

models_dir="${1:-${MODELS_DIR:-/opt/models}}"
gid_of() { getent group "$1" | cut -d: -f3; }

[ -e /dev/dri ] || echo "warning: /dev/dri not found; the GPU won't be visible in the container" >&2
command -v vulkaninfo >/dev/null && vulkaninfo --summary 2>/dev/null | grep -E 'deviceName|driverName' | sed 's/^/host vulkan: /' || true

if [ -f .env ]; then
  cp .env .env.bak
  echo "existing .env saved as .env.bak"
fi
sed \
  -e "s|^MODELS_DIR=.*|MODELS_DIR=${models_dir}|" \
  -e "s|^KILN_UID=.*|KILN_UID=$(id -u)|" \
  -e "s|^KILN_GID=.*|KILN_GID=$(id -g)|" \
  -e "s|^VIDEO_GID=.*|VIDEO_GID=$(gid_of video || echo 44)|" \
  -e "s|^RENDER_GID=.*|RENDER_GID=$(gid_of render || echo 993)|" \
  .env.example > .env

mkdir -p data "${models_dir}"
echo "wrote .env:"
grep -E '^(MODELS_DIR|KILN_UID|KILN_GID|VIDEO_GID|RENDER_GID)=' .env | sed 's/^/  /'
