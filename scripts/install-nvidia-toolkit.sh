#!/usr/bin/env bash
# Install the NVIDIA Container Toolkit so Docker containers can use the GPU (NVIDIA hosts only).
# Needs the NVIDIA driver installed and working on the host (`nvidia-smi` runs) and Docker installed.
# Run once per box: ./scripts/install-nvidia-toolkit.sh   (asks for your sudo password)
set -euo pipefail

command -v nvidia-smi >/dev/null && nvidia-smi -L || { echo "nvidia-smi not found or no GPU: install the NVIDIA driver first." >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker isn't installed: run ./scripts/install-docker.sh first." >&2; exit 1; }

if ! command -v nvidia-ctk >/dev/null; then
  # NVIDIA's apt repository, as documented at docs.nvidia.com/datacenter/cloud-native/container-toolkit
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y nvidia-container-toolkit
fi

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

echo
echo "Checking that a container can see the GPU:"
docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
