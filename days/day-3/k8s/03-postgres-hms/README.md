# Phase 3 — PostgreSQL + Hive Metastore

Two tightly-coupled components:

- **PostgreSQL** — backend store for HMS metadata (tables, partitions, column stats). One StatefulSet, one PVC.
- **Hive Metastore** — Thrift service on `:9083` that Spark and StarRocks will call. No data flows through it; it's a catalog.

## File layout

| File                              | What it declares |
|-----------------------------------|------------------|
| `postgres-secret.yaml`            | DB user/password/database name |
| `postgres-service.yaml`           | ClusterIP + headless Services for Postgres |
| `postgres-statefulset.yaml`       | Postgres 16 StatefulSet, 10Gi PVC |
| `s3a-credentials-secret.yaml`     | AWS-style env vars for HMS S3A → MinIO |
| `hms-configmap.yaml`              | `hive-site.xml` (JDBC + S3A + Thrift config) |
| `hms-deployment.yaml`             | `apache/hive:4.0.0` in `SERVICE_NAME=metastore` mode; initContainer fetches the Postgres JDBC driver; entrypoint runs `-initOrUpgradeSchema` on startup (idempotent) |
| `hms-service.yaml`                | ClusterIP `hive-metastore:9083` Thrift endpoint |

## Apply order

The Makefile applies these in the right order and waits between steps:

```
postgres-secret        →  postgres-statefulset  →  (Ready)
postgres-service
↓
s3a-credentials        →  hms-configmap        →  hms-deployment  →  hms-service  →  (Ready)
```

No separate schema-init Job — the Hive 4 entrypoint invokes `schematool -initOrUpgradeSchema` itself, which is idempotent. The only ordering constraint is that Postgres must be Ready before HMS starts; if it isn't, HMS crash-loops a few times and self-recovers once Postgres accepts connections.

## Production mapping

- Postgres → managed RDS Postgres (single-AZ dev, multi-AZ prod)
- HMS → 2× Deployment replicas behind a Service, same `hive-site.xml`, same DB
- Same Thrift endpoint pattern: one stateless RPC layer, one stateful DB behind it

## Key learning point

HMS is **not reactive to S3**. Writing files to `s3a://analytics-lab/warehouse/...` directly does not register them as partitions. Recovery:

- `MSCK REPAIR TABLE <tbl>` — listing-diff, adds missing partitions
- `ALTER TABLE <tbl> ADD PARTITION (zoneId=N) LOCATION 's3a://...'` — explicit

Experiment 06 in Phase 7 demonstrates this drift.
