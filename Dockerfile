# Kiln: llama.cpp (Vulkan) + web control panel.
#
#   docker compose build            # uses the args below
#   docker compose build --build-arg LLAMA_CPP_REF=b11200
#
ARG UBUNTU_VERSION=26.04
ARG NODE_VERSION=24

# ---------------------------------------------------------------- llama.cpp source
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS src
ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp
# The commit this box was tuned on (b11081+13). A tag (b11200) or branch works too.
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

# ---------------------------------------------------------------- llama.cpp build (Vulkan)
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates libssl-dev \
        libvulkan-dev glslc spirv-headers \
    && rm -rf /var/lib/apt/lists/*
COPY --from=src /src /src
COPY --from=webui /ui/dist /src/tools/ui/dist
WORKDIR /src
# Portable CPU code (all variants, picked at runtime) so one image runs on every box;
# the GPU work happens in the Vulkan backend.
RUN cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_VULKAN=ON \
        -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --config Release -j"$(nproc)"
RUN mkdir -p /out \
    && cp build/bin/llama-server build/bin/llama-bench build/bin/llama-cli /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \;

# ---------------------------------------------------------------- runtime
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 mesa-vulkan-drivers libgomp1 libssl3t64 curl ca-certificates \
        python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN python3 -m venv /app/venv && /app/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY --from=build /out /app/llama
COPY app/kiln /app/kiln/kiln
COPY web /app/web

ENV KILN_LLAMA_SERVER=/app/llama/llama-server \
    LD_LIBRARY_PATH=/app/llama \
    KILN_WEB_DIR=/app/web \
    KILN_DATA_DIR=/data \
    KILN_MODELS_DIR=/models \
    HF_HOME=/models/hf \
    HOME=/data \
    XDG_CACHE_HOME=/data/cache \
    MESA_SHADER_CACHE_DIR=/data/cache/mesa \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/kiln
EXPOSE 8090 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8090/healthz || exit 1
STOPSIGNAL SIGTERM
CMD ["/app/venv/bin/python", "-m", "kiln"]
