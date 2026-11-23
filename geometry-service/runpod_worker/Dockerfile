FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

WORKDIR /app
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY runpod_worker/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY fast_mvp /app/fast_mvp
COPY runpod_worker /app/runpod_worker

ENV PYTHONPATH=/app
ENV CATALOG_DIR=/app/fast_mvp/catalog
CMD ["python3", "-m", "runpod_worker.worker"]
