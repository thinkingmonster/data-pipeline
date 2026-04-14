#!/usr/bin/env bash
# Verify MinIO: pod ready, bucket layout exists, write+read round-trips.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/4] MinIO StatefulSet ready?"
$KUBECTL -n "$NS" rollout status statefulset/minio --timeout=60s
green "  OK"

blue "[2/4] Bucket layout exists?"
$KUBECTL -n "$NS" run mc-check --rm -it --restart=Never \
  --image=minio/mc:RELEASE.2024-10-08T09-37-26Z \
  --env="MINIO_ROOT_USER=minioadmin" \
  --env="MINIO_ROOT_PASSWORD=minioadmin" \
  --command -- /bin/sh -c '
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    mc ls local/analytics-lab/
  '

blue "[3/4] Round-trip write+read?"
$KUBECTL -n "$NS" run mc-rw --rm -it --restart=Never \
  --image=minio/mc:RELEASE.2024-10-08T09-37-26Z \
  --env="MINIO_ROOT_USER=minioadmin" \
  --env="MINIO_ROOT_PASSWORD=minioadmin" \
  --command -- /bin/sh -c '
    set -e
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    echo "hello-from-day-1" > /tmp/hello.txt
    mc cp /tmp/hello.txt local/analytics-lab/bronze/_smoke/hello.txt
    mc cat local/analytics-lab/bronze/_smoke/hello.txt
    mc rm local/analytics-lab/bronze/_smoke/hello.txt
  '

blue "[4/4] Service endpoints?"
$KUBECTL -n "$NS" get svc minio
green "MinIO verified."
