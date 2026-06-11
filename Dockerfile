# syntax=docker/dockerfile:1
# EKS nodes are typically linux/amd64. Build with:
#   docker build --platform linux/amd64 -t <ecr>/otel-ai-agent-sim:latest .
#
# BuildKit cache speeds pip on rebuilds; first amd64 build on Apple Silicon via QEMU can still take many minutes.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app.py prometheus_rw.py otlp_metrics.py .
COPY sim sim/
COPY prompb prompb/

USER nobody

CMD ["python", "/app/app.py"]
