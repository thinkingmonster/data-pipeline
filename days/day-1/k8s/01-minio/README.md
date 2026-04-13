# Phase 1a — MinIO (S3-compatible object storage)

Single-node MinIO as a `StatefulSet` with a 50 Gi PVC. Replaces Amazon S3 locally.

## Resources

| Kind          | Name                  | Purpose                                       |
|---------------|-----------------------|-----------------------------------------------|
| Secret        | `minio-credentials`   | root user/password (lab-only: minioadmin/minioadmin) |
| Service       | `minio`               | API :9000, console :9001                      |
| Service       | `minio-headless`      | stable pod DNS for the StatefulSet            |
| StatefulSet   | `minio`               | one replica, 50 Gi PVC mounted at `/data`     |
| Job           | `minio-bucket-init`   | creates `analytics-lab/{bronze,silver,checkpoints}` |

## Deploy

```bash
make minio
```

## Verify

```bash
make verify-minio
```

## Browse the console

```bash
make minio-console
# then visit http://localhost:9001  (minioadmin / minioadmin)
```

## Why these design choices

- **StatefulSet, not Deployment** — MinIO is stateful; the PVC must follow the pod identity. A Deployment would lose data on rescheduling.
- **Single replica in the lab** — production MinIO clusters use erasure-coded pools across 4+ nodes. We're optimizing for "fits on one laptop" not durability.
- **Bucket layout (`bronze/`, `silver/`, `checkpoints/`)** — Spark will write Delta tables under `bronze/` and `silver/`; structured-streaming checkpoints go in `checkpoints/`. Setting this up now avoids ad-hoc directory creation later.

## The mental model

S3 is **not a filesystem.**
- No atomic rename — uploads are `put-if-absent` for new keys, but in-place rewrites are not transactional.
- No directories — `bronze/` is just a key prefix. Listing it requires a `ListObjectsV2` API call, which is paged.
- Eventually-consistent listings (historically) — newer S3 is read-after-write consistent for new objects, but list operations can still lag.

Why Delta Lake works at all on object storage: it serializes commits via `_delta_log/NNN.json` files using `put-if-absent` semantics. Two writers committing version `N` will collide; one wins, one retries. MinIO supports this primitive.

## Production mapping

 MinIO is S3-API-compatible, so Spark/StarRocks client code is identical — only `s3 endpoint` differs.
