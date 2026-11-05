# Kiln: llama.cpp + audio.cpp + web control panel. One Dockerfile, three GPU backends:
#
#   KILN_BACKEND=vulkan   AMD (and Intel) GPUs through Mesa RADV/ANV   (default)
#   KILN_BACKEND=cuda     NVIDIA GPUs through CUDA
#   KILN_BACKEND=rocm     AMD GPUs through ROCm, on Docker Desktop for Windows (WSL2, /dev/dxg)
#
#   docker compose build                      # args come from .env (scripts/setup-env.sh)
#   docker compose build --build-arg LLAMA_CPP_REF=b11200
#   docker compose build --build-arg AUDIO_CPP_REF=none   # leave audio.cpp out of the image
#
ARG KILN_BACKEND=vulkan
ARG UBUNTU_VERSION=26.04
ARG NODE_VERSION=24
# CUDA images: the host driver must support this CUDA version (12.8 needs driver 570+).
ARG CUDA_VERSION=12.8.1
ARG CUDA_UBUNTU_VERSION=24.04
# ROCm images. librocdxg is AMD's ROCm runtime for WSL (it reaches the GPU through /dev/dxg); its
# release has to match the ROCm version (github.com/ROCm/librocdxg, "WSL Compatibility Matrix").
ARG ROCM_VERSION=7.2.4
ARG ROCM_UBUNTU_VERSION=24.04
ARG ROCDXG_VERSION=1.2.2
ARG ROCDXG_SHA256=28ded1254811192ebace1f76c0227580184af7b27ab2475fb9728295a702d541
# The GPUs ROCm supports on WSL: RX 7900/W7900/W7800, RX 7800/W7700, Ryzen AI 300, Ryzen AI Max, RX 9060, RX 9070/R9700.
ARG ROCM_WSL_ARCHITECTURES="gfx1100;gfx1101;gfx1150;gfx1151;gfx1200;gfx1201"

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

# ---------------------------------------------------------------- audio.cpp source
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS audiocpp-src
ARG AUDIO_CPP_REPO=https://github.com/0xShug0/audio.cpp
# tag v0.9.2; a tag or branch works too. `none` leaves audio.cpp out of the image.
ARG AUDIO_CPP_REF=0097d365a00b52a6991ad7618f0a04e8d2935e47
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# audio.cpp's .gitmodules point at git@github.com:..., and the image has no ssh, so fetch submodules over https.
RUN if [ "${AUDIO_CPP_REF}" = "none" ]; then mkdir -p /src; else \
      git config --global url."https://github.com/".insteadOf "git@github.com:" \
      && git clone "${AUDIO_CPP_REPO}" /src \
      && cd /src && git checkout --quiet "${AUDIO_CPP_REF}" \
      && git submodule update --init --recursive --depth 1 \
      && git log -1 --format='audio.cpp %h (%cd)' --date=short; \
    fi

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

# ---------------------------------------------------------------- build: ROCm
FROM docker.io/rocm/dev-ubuntu-${ROCM_UBUNTU_VERSION}:${ROCM_VERSION} AS rocm-dev
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates libssl-dev hipblas-dev rocblas-dev \
    && rm -rf /var/lib/apt/lists/*

FROM rocm-dev AS build-rocm
# GPU architectures to compile for, e.g. "gfx1100" (RX 7900). "default" = every GPU ROCm supports on WSL.
ARG ROCM_ARCHITECTURES=default
ARG ROCM_WSL_ARCHITECTURES
COPY --from=src /src /src
COPY --from=webui /ui/dist /src/tools/ui/dist
WORKDIR /src
RUN TARGETS="${ROCM_ARCHITECTURES}"; if [ "${TARGETS}" = default ]; then TARGETS="${ROCM_WSL_ARCHITECTURES}"; fi \
    && HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_HIP=ON -DGPU_TARGETS="${TARGETS}" \
        -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --config Release -j"$(nproc)"
RUN mkdir -p /out \
    && cp build/bin/llama-server build/bin/llama-bench build/bin/llama-cli /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \;

# ---------------------------------------------------------------- build: audio.cpp, Vulkan
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS audiocpp-build-vulkan
ARG AUDIO_CPP_REF
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates libssl-dev \
        libvulkan-dev glslc spirv-headers \
    && rm -rf /var/lib/apt/lists/*
COPY --from=audiocpp-src /src /src
WORKDIR /src
# Same portable CPU policy as llama.cpp: all CPU variants, picked at runtime.
RUN if [ "${AUDIO_CPP_REF}" = "none" ]; then mkdir -p /out; else \
      cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DAUDIOCPP_MODEL_SET=full \
        -DENGINE_ENABLE_NATIVE_CPU=OFF -DENGINE_ENABLE_CPU_ALL_VARIANTS=ON \
        -DENGINE_ENABLE_OPENMP=ON -DAUDIOCPP_DEPLOYMENT_BUILD=ON \
        -DENGINE_BUILD_EXAMPLES=OFF -DENGINE_BUILD_TESTS=OFF -DENGINE_BUILD_WARMBENCH=OFF \
        -DENGINE_ENABLE_VULKAN=ON -DENGINE_ENABLE_CUDA=OFF \
    && cmake --build build --parallel "$(nproc)" --target audiocpp_server \
    && mkdir -p /out \
    && cp build/bin/audiocpp_server /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \; \
    && cp -r model_specs /out/; \
    fi

# ---------------------------------------------------------------- build: audio.cpp, CUDA
FROM docker.io/nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${CUDA_UBUNTU_VERSION} AS audiocpp-build-cuda
ARG AUDIO_CPP_REF
# Same meaning as in build-cuda above.
ARG CUDA_ARCHITECTURES=default
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-14 g++-14 build-essential cmake git ca-certificates libssl-dev \
    && rm -rf /var/lib/apt/lists/*
ENV CC=gcc-14 CXX=g++-14 CUDAHOSTCXX=g++-14
COPY --from=audiocpp-src /src /src
WORKDIR /src
RUN if [ "${AUDIO_CPP_REF}" = "none" ]; then mkdir -p /out; else \
      ARCH=""; if [ "${CUDA_ARCHITECTURES}" != "default" ]; then ARCH="-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES}"; fi \
    && cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DAUDIOCPP_MODEL_SET=full \
        -DENGINE_ENABLE_NATIVE_CPU=OFF -DENGINE_ENABLE_CPU_ALL_VARIANTS=ON \
        -DENGINE_ENABLE_OPENMP=ON -DAUDIOCPP_DEPLOYMENT_BUILD=ON \
        -DENGINE_BUILD_EXAMPLES=OFF -DENGINE_BUILD_TESTS=OFF -DENGINE_BUILD_WARMBENCH=OFF \
        -DENGINE_ENABLE_CUDA=ON ${ARCH} \
        -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined \
    && cmake --build build --parallel "$(nproc)" --target audiocpp_server \
    && mkdir -p /out \
    && cp build/bin/audiocpp_server /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \; \
    && cp -r model_specs /out/; \
    fi

# ---------------------------------------------------------------- build: audio.cpp, ROCm
FROM rocm-dev AS audiocpp-build-rocm
ARG AUDIO_CPP_REF
ARG ROCM_ARCHITECTURES=default
ARG ROCM_WSL_ARCHITECTURES
COPY --from=audiocpp-src /src /src
WORKDIR /src
# Not the all-CPU-variants build here: that loads backends as plug-ins, and Fish Audio's HIP sampler calls into
# the HIP backend directly, which then doesn't link. The CPU code is the portable baseline instead, and the
# static HIP backend has to be position-independent to link into the server.
RUN if [ "${AUDIO_CPP_REF}" = "none" ]; then mkdir -p /out; else \
      TARGETS="${ROCM_ARCHITECTURES}"; if [ "${TARGETS}" = default ]; then TARGETS="${ROCM_WSL_ARCHITECTURES}"; fi \
    && HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DAUDIOCPP_MODEL_SET=full \
        -DENGINE_ENABLE_NATIVE_CPU=OFF -DENGINE_ENABLE_CPU_ALL_VARIANTS=OFF \
        -DENGINE_ENABLE_OPENMP=ON -DAUDIOCPP_DEPLOYMENT_BUILD=ON \
        -DENGINE_BUILD_EXAMPLES=OFF -DENGINE_BUILD_TESTS=OFF -DENGINE_BUILD_WARMBENCH=OFF \
        -DENGINE_ENABLE_HIP=ON -DENGINE_ENABLE_CUDA=OFF -DENGINE_ENABLE_VULKAN=OFF -DGPU_TARGETS="${TARGETS}" \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    && cmake --build build --parallel "$(nproc)" --target audiocpp_server \
    && mkdir -p /out \
    && cp build/bin/audiocpp_server /out/ \
    && find build -name "*.so*" -exec cp -P {} /out/ \; \
    && cp -r model_specs /out/; \
    fi

# ---------------------------------------------------------------- runtime bases
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS base-vulkan
# libespeak-ng1: audio.cpp's Kokoro (and Piper) turn text into phonemes with eSpeak-ng, loaded at run time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libvulkan1 mesa-vulkan-drivers libgomp1 libssl3t64 curl ca-certificates python3 python3-venv \
        libespeak-ng1 espeak-ng-data \
    && rm -rf /var/lib/apt/lists/*
ENV XDG_CACHE_HOME=/data/cache MESA_SHADER_CACHE_DIR=/data/cache/mesa

FROM docker.io/nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${CUDA_UBUNTU_VERSION} AS base-cuda
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libssl3t64 curl ca-certificates python3 python3-venv \
        libespeak-ng1 espeak-ng-data \
    && rm -rf /var/lib/apt/lists/*
# The NVIDIA container toolkit mounts the driver and nvidia-smi (used for GPU telemetry).
ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    CUDA_CACHE_PATH=/data/cache/cuda

FROM docker.io/ubuntu:${ROCM_UBUNTU_VERSION} AS base-rocm
ARG ROCM_VERSION
ARG ROCDXG_VERSION
ARG ROCDXG_SHA256
ARG ROCM_ARCHITECTURES=default
ARG ROCM_WSL_ARCHITECTURES
# AMD's apt repository for the ROCm runtime (hipBLAS pulls in rocBLAS, rocSOLVER, hipBLASLt and the HIP runtime),
# then librocdxg, the ROCm runtime's way to the GPU under WSL (after ROCm, so /opt/rocm is ROCm's symlink).
# rocBLAS and hipBLASLt ship GPU kernels for every architecture (about 5 GB, mostly datacenter GPUs): keep only
# the ones this image was built for, in the same layer so the rest never lands in the image.
RUN TARGETS="${ROCM_ARCHITECTURES}"; if [ "${TARGETS}" = default ]; then TARGETS="${ROCM_WSL_ARCHITECTURES}"; fi \
    && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key | gpg --dearmor -o /etc/apt/keyrings/rocm.gpg \
    && . /etc/os-release \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/${ROCM_VERSION} ${VERSION_CODENAME} main" \
        > /etc/apt/sources.list.d/rocm.list \
    && printf 'Package: *\nPin: release o=repo.radeon.com\nPin-Priority: 600\n' > /etc/apt/preferences.d/rocm-pin-600 \
    && curl -fsSL -o /tmp/rocdxg.deb \
        "https://github.com/ROCm/librocdxg/releases/download/v${ROCDXG_VERSION}/rocdxg-roct_${ROCDXG_VERSION}_amd64.deb" \
    && echo "${ROCDXG_SHA256}  /tmp/rocdxg.deb" | sha256sum -c - \
    && apt-get update && apt-get install -y --no-install-recommends \
        hipblas libgomp1 libssl3t64 python3 python3-venv libespeak-ng1 espeak-ng-data \
    && apt-get install -y --no-install-recommends /tmp/rocdxg.deb \
    && test -L /opt/rocm \
    && KEEP="$(echo "${TARGETS}" | tr ';' '|')" \
    && find /opt/rocm/lib/rocblas/library /opt/rocm/lib/hipblaslt/library -type f -name '*gfx*' \
        | grep -vE "(${KEEP})([^0-9a-z]|$)" | xargs -r rm -f \
    && du -sh /opt/rocm/lib/rocblas/library /opt/rocm/lib/hipblaslt/library \
    && rm -rf /var/lib/apt/lists/* /tmp/rocdxg.deb
ENV PATH=/opt/rocm/bin:${PATH}

FROM build-${KILN_BACKEND} AS llama
FROM audiocpp-build-${KILN_BACKEND} AS audiocpp

# ---------------------------------------------------------------- runtime
FROM base-${KILN_BACKEND} AS runtime
ARG KILN_BACKEND
COPY app/requirements.txt /app/requirements.txt
RUN python3 -m venv /app/venv && /app/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

COPY --from=llama /out /app/llama
COPY --from=audiocpp /out /app/audiocpp
COPY app/kiln /app/kiln/kiln
COPY web /app/web

# /app/audiocpp is not on the global LD_LIBRARY_PATH: its ggml libraries would clash with llama.cpp's.
# Kiln sets the path per process when it starts audio.cpp.
ENV KILN_BACKEND=${KILN_BACKEND} \
    KILN_LLAMA_SERVER=/app/llama/llama-server \
    KILN_AUDIOCPP_SERVER=/app/audiocpp/audiocpp_server \
    KILN_AUDIOCPP_LIB_DIR=/app/audiocpp \
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
