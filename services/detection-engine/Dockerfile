FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS gpu

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3-pip python3.11-dev \
    libgl1 libglib2.0-0 ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV YOLO_MODEL_PATH=/models/yolo11n.pt
VOLUME ["/models"]

CMD ["python", "-m", "detection.main"]
