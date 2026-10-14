#!/usr/bin/env bash
# Install Docker Engine + Compose + Buildx from Ubuntu's own packages and let you use it without sudo.
# Run once per box: ./scripts/install-docker.sh   (asks for your sudo password)
set -euo pipefail

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "Docker and Compose are already installed: $(docker --version)"
  exit 0
fi

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo
echo "Docker installed: $(docker --version)"
echo "You were added to the 'docker' group. Log out and back in (or run 'newgrp docker')"
echo "before using docker without sudo."
