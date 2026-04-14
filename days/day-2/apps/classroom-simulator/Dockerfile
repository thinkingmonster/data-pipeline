# syntax=docker/dockerfile:1.7
# Multi-arch: built via `docker buildx build --platform linux/amd64,linux/arm64`.

FROM --platform=$BUILDPLATFORM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /install /usr/local
COPY sim/ ./sim/
USER 65532:65532
CMD ["python", "-m", "sim.simulator"]
