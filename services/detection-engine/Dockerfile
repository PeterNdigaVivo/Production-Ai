# Detection engine — two independent targets in one Dockerfile.
#
#   docker build --target cpu   -f Dockerfile -t detection-engine:cpu .   # default
#   docker build --target gpu   -f Dockerfile -t detection-engine:gpu .
#
# `cpu` is the FIRST stage so `--target` unset picks it. It installs the CPU
# torch wheels from PyPI's pytorch-cpu index (see requirements.txt), bakes
# yolo11n.pt into /opt/models at build time (the /models volume mount at
# runtime would otherwise start empty), and an entrypoint script seeds
# YOLO_MODEL_PATH from the baked copy on first run.
#
# `gpu` is preserved for the eventual factory GPU box. Its Python install
# was previously broken: `ln -sf python3.11 /usr/local/bin/python` while
# `python3-pip` installed packages against Ubuntu's default python3.10 —
# every `python -m …` failed to import. Fixed here by bootstrapping pip
# for python3.11 via ensurepip and using `python3.11 -m pip` explicitly.
# The gpu target reads requirements-gpu.txt (CUDA torch, onnxruntime-gpu).

# ============================================================================
# CPU TARGET — default. This is what runs on the CPU tower.
# ============================================================================
FROM python:3.12-slim AS cpu

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU torch wheels from PyTorch's dedicated CPU index in a SEPARATE
# layer so it caches independently of the app deps below. Torch is the heavy
# ~200MB install; app-requirement churn should not invalidate it.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.4.1 torchvision==0.19.1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the YOLO model at BUILD time so the container starts without a
# network round-trip and doesn't rely on the /models volume being pre-seeded.
# Ultralytics downloads to CWD by default; guard by moving from either
# possible location. Verify the file lands at /opt/models/yolo11n.pt.
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" \
 && mkdir -p /opt/models \
 && if [ -f yolo11n.pt ]; then cp yolo11n.pt /opt/models/yolo11n.pt; \
    elif [ -f /root/.config/Ultralytics/yolo11n.pt ]; then cp /root/.config/Ultralytics/yolo11n.pt /opt/models/yolo11n.pt; \
    else cp "$(find / -name yolo11n.pt -not -path '/proc/*' 2>/dev/null | head -1)" /opt/models/yolo11n.pt; \
    fi \
 && test -f /opt/models/yolo11n.pt

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV YOLO_MODEL_PATH=/models/yolo11n.pt
VOLUME ["/models"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "detection.main"]


# ============================================================================
# GPU TARGET — preserved for the eventual factory GPU box.
# ============================================================================
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS gpu

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Install python3.11 AND bootstrap pip against python3.11 (not python3.10,
# which is Ubuntu 22.04's default and what apt's python3-pip targets).
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3.11-venv \
    libgl1 libglib2.0-0 ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3.11 -m ensurepip --upgrade \
 && python3.11 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
 && ln -sf /usr/bin/python3.11 /usr/local/bin/python \
 && ln -sf /usr/bin/python3.11 /usr/local/bin/python3

WORKDIR /app

COPY requirements-gpu.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements-gpu.txt

COPY . .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV YOLO_MODEL_PATH=/models/yolo11n.pt
VOLUME ["/models"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "detection.main"]
