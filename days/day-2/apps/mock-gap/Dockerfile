# syntax=docker/dockerfile:1.7
# Multi-arch: built via `docker buildx build --platform linux/amd64,linux/arm64`.
# BuildKit auto-populates TARGETPLATFORM/TARGETARCH; python:slim has both.

FROM --platform=$BUILDPLATFORM python:3.12-slim AS builder
ARG TARGETARCH
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ ./app/
EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
