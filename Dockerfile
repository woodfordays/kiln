# Kiln: llama.cpp + web control panel. One Dockerfile, two GPU backends:
#
#   KILN_BACKEND=vulkan   AMD (and Intel) GPUs through Mesa RADV/ANV   (default)
#   KILN_BACKEND=cuda     NVIDIA GPUs through CUDA
#
#   docker compose build                      # args come from .env (scripts/setup-env.sh)
#   docker compose build --build-arg LLAMA_CPP_REF=b11200
#
ARG KILN_BACKEND=vulkan
ARG UBUNTU_VERSION=26.04
ARG NODE_VERSION=24
# CUDA images: the host driver must support this CUDA version (12.8 needs driver 570+).
ARG CUDA_VERSION=12.8.1
ARG CUDA_UBUNTU_VERSION=24.04

# ---------------------------------------------------------------- llama.cpp source
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS src
ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp
# The commit the AMD box was tuned on (b11081+13). A tag (b11200) or branch works too.
ARG LLAMA_CPP_REF=ff0dbb975e93a9a2899efa34bdd32d1c5cfbc183
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Treeless clone: full commit history (llama.cpp derives its build number from it), files only for the ref.
RUN git clone --filter=tree:0 --no-checkout "${LLAMA_CPP_REPO}" /src \
    && cd /src && git checkout --quiet "${LLAMA_CPP_REF}" \
    && git log -1 --format='llama.cpp %h (%cd)' --date=short

# ---------------------------------------------------------------- llama.cpp's own chat UI
FROM docker.io/node:${NODE_VERSION} AS webui
COPY --from=src /src/tools/ui /ui
WORKDIR /ui
RUN npm ci --no-audit --no-fund && npm run build

# ---------------------------------------------------------------- build: Vulkan
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS build-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates libssl-dev \
        libvulkan-dev glslc spirv-headers \
    && rm -rf /var/lib/apt/lists/*
COPY --from=src /src /src
COPY --from=webui /ui/dist /src/tools/ui/dist
WORKDIR /src
# Portable CPU code (all variants, picked at runtime) so one image runs on every box.
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_VULKAN=ON \
        -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --config Release -j"$(nproc)"
RUN mkdir -p /out \
    && cp build/bin/llama-server build/bin/llama-bench build/bin/llama-cli /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \;

# ---------------------------------------------------------------- build: CUDA
FROM docker.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${CUDA_UBUNTU_VERSION} AS build-cuda
# GPU architectures to compile for, e.g. "86;89" (RTX 30/40) or "120" (RTX 50).
# "default" builds every architecture CUDA supports: portable, but a much longer build.
ARG CUDA_ARCHITECTURES=default
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-14 g++-14 build-essential cmake git ca-certificates libssl-dev \
    && rm -rf /var/lib/apt/lists/*
ENV CC=gcc-14 CXX=g++-14 CUDAHOSTCXX=g++-14
COPY --from=src /src /src
COPY --from=webui /ui/dist /src/tools/ui/dist
WORKDIR /src
RUN if [ "${CUDA_ARCHITECTURES}" != "default" ]; then ARCH="-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES}"; fi \
    && cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON ${ARCH} \
        -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
        -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined \
    && cmake --build build --config Release -j"$(nproc)"
RUN mkdir -p /out \
    && cp build/bin/llama-server build/bin/llama-bench build/bin/llama-cli /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \;

# ---------------------------------------------------------------- runtime bases
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS base-vulkan
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 mesa-vulkan-drivers libgomp1 libssl3t64 curl ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
ENV XDG_CACHE_HOME=/data/cache MESA_SHADER_CACHE_DIR=/data/cache/mesa

FROM docker.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${CUDA_UBUNTU_VERSION} AS base-cuda
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libssl3t64 curl ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
# The NVIDIA container toolkit mounts the driver and nvidia-smi (used for GPU telemetry).
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    CUDA_CACHE_PATH=/data/cache/cuda

FROM build-${KILN_BACKEND} AS llama

# ---------------------------------------------------------------- runtime
FROM base-${KILN_BACKEND} AS runtime
ARG KILN_BACKEND
COPY app/requirements.txt /app/requirements.txt
RUN python3 -m venv /app/venv && /app/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY --from=llama /out /app/llama
COPY app/kiln /app/kiln/kiln
COPY web /app/web

ENV KILN_BACKEND=${KILN_BACKEND} \
    KILN_LLAMA_SERVER=/app/llama/llama-server \
    LD_LIBRARY_PATH=/app/llama${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} \
    KILN_WEB_DIR=/app/web \
    KILN_DATA_DIR=/data \
    KILN_MODELS_DIR=/models \
    HF_HOME=/models/hf \
    HOME=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/kiln
EXPOSE 8090 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8090/healthz || exit 1
STOPSIGNAL SIGTERM
CMD ["/app/venv/bin/python", "-m", "kiln"]
