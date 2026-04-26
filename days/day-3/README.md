# Day 3 — Catalog Layer: PostgreSQL + Hive Metastore

**Today you add the piece that makes `SELECT * FROM tbl_analytics_bronze` possible.** Not the data — the data still lives in MinIO. The *naming*. The layer that lets Spark say "please write partition zoneId=3 of my bronze table" and StarRocks say "show me that partition" and both resolve to the same `s3a://...` path. Welcome to the phone book.

Today's work covers **Phase 3** from [../../LEARNING-ROADMAP.md](../../LEARNING-ROADMAP.md). It depends on Days 1–2 (Minikube, Kafka, Schema Registry, MinIO, Mock GAP, Classroom Simulator — all carried forward unchanged).

---

## TL;DR

You'll:

1. Stand up **PostgreSQL** as the metadata backend for Hive Metastore. StatefulSet + PVC, because losing this DB means losing every table definition.
2. Deploy **Hive Metastore** in standalone mode (`SERVICE_NAME=metastore`) speaking Thrift on `:9083`. The image's entrypoint runs `schematool -initOrUpgradeSchema` automatically on startup — idempotent in Hive 4, so no separate Job needed.
3. Work around two quirks of `apache/hive:4.0.0`: it doesn't ship the Postgres JDBC driver, and its default user can't write to `/opt/hive/lib`. An `initContainer` fetches the driver, and the pod runs as root (lab-only concession).
4. Configure HMS to treat MinIO as its warehouse via S3A, using `EnvironmentVariableCredentialsProvider` so `hive-site.xml` stays credential-free.
5. Prove it works end-to-end: schema version reported, Postgres has the HMS tables, Thrift port open, `metatool -listFSRoot` returns the S3A path.

Nothing writes to HMS yet — that's Spark's job in Phase 4. Today you're standing up the catalog infrastructure and learning what's inside it.

Time budget: 2–3 hours focused. More if you sit with the docs doc ([../../docs/hive-metastore.md](../../docs/hive-metastore.md)) and the "points to ponder" section below.

---

## Prerequisites

Same as Days 1–2: `minikube` (≥ 1.33), `kubectl`, `docker` with buildx, `make`, `curl`, Python 3.12, ~30 GB free disk.

Run all commands from `days/day-3/` unless noted.

---

## Architecture — the Phase 3 slice

```
 ┌────────────────────────────────────────────────────────┐
 │  apache/hive:4.0.0  (SERVICE_NAME=metastore)            │
 │                                                         │
 │    Thrift server on :9083  (what Spark + StarRocks hit) │
 │    │                                                    │
 │    ▼                                                    │
 │    DataNucleus / JDO ──► Postgres JDBC driver           │
 │                              │                          │
 │    hive-site.xml config:     │                          │
 │      ConnectionURL = jdbc:postgresql://hms-postgres:5432│
 │      warehouse.dir = s3a://analytics-lab/warehouse      │
 │      fs.s3a.endpoint = http://minio:9000                │
 │      aws.credentials.provider = EnvironmentVariable…    │
 └───────────┬────────────────────────┬───────────────────┘
             │                        │
             ▼                        ▼
    ┌─────────────────┐      ┌─────────────────┐
    │   Postgres 16   │      │   MinIO (S3)    │
    │                 │      │                 │
    │  DB: metastore  │      │  analytics-lab/ │
    │  ~60 HMS tables │      │    warehouse/   │
    │  (DBS, TBLS,    │      │                 │
    │   PARTITIONS…)  │      │  (empty today — │
    │                 │      │   Phase 4 fills │
    │                 │      │   this)         │
    └─────────────────┘      └─────────────────┘
```

One flow direction today: compute (HMS) ↔ metadata (Postgres). The compute ↔ storage path is wired but unused until Spark (Phase 4) writes Delta files.

---

## What you'll have when you're done

- A `hms-postgres` StatefulSet with a 10Gi PVC, database `metastore`, ~60 HMS schema tables populated
- A `hive-metastore-schema-init` Job in `Completed` state (1/1 succeeded)
- A `hive-metastore` Deployment, 1/1 Ready, serving Thrift on `:9083`
- A `hive-metastore` Service resolvable by name from any pod in `data-pipeline`
- Zero bytes written to MinIO — the catalog is warehouse-aware, but nothing has written a table yet

---

## Core concepts you need to internalize

Read each of these twice. The full treatment is in [../../docs/hive-metastore.md](../../docs/hive-metastore.md) — this is the condensed version.

### 1. Catalog ≠ storage

HMS stores `table → path` mappings. It does not store data. MinIO stores data. It does not store table definitions. Neither can tell you a *correct* answer without the other. This separation is the lakehouse's defining feature — and its single biggest source of operational bugs.

A useful mental model: HMS is the library catalog card. The book is in the stacks. The card tells you where to find the book. If someone adds a book without updating the card, you can't find it. If someone updates the card but the book is missing, you get to the shelf and find nothing.

### 2. HMS is synchronous and pull-based

Clients (Spark, StarRocks) call HMS via Thrift RPC. HMS calls Postgres via JDBC. Every call = one network round trip + one DB transaction. There is no push, no pubsub, no event stream. If Spark writes a new partition and then StarRocks asks "what partitions exist?", StarRocks sees it only if:

- Spark's write path called `append_partition` on HMS (Delta writer does this when `hive.metastore.uris` is set), **and**
- StarRocks's query path called `get_partitions` fresh (not served from a cached catalog)

Miss either, and you see stale data. Experiment 10 in Phase 7 is specifically about this.

### 3. `schematool -initSchema` is the migration runner

HMS will not auto-create its own schema. If you start an HMS pod against an empty database, it boots and immediately errors:
```
MetaException: Version information not found in metastore.
```
You must explicitly run `schematool -initSchema -dbType postgres` once. This creates the ~60 tables HMS expects (`DBS`, `TBLS`, `PARTITIONS`, `SDS`, `COLUMNS_V2`, …).

In the lab we run it as a one-shot Job. In production you run it as a pre-flight before the first HMS rollout, and `-upgradeSchema` before every subsequent version bump.

**Non-idempotent by default.** A second `initSchema` against an already-initialized DB errors out. The init Job's script grep-checks `-info` output to tolerate this for lab re-runs.

### 4. `hive-site.xml` is the one config

HMS reads one file at startup. Everything else — JDBC URL, warehouse location, S3A endpoint, Thrift port — is a property in that file. No other config path. No env var overrides (mostly). This is 2009-era design and it shows, but it's what you inherit when you run HMS.

Two things you do *not* want in that file in prod:

1. **Database passwords.** In the lab we inline them (they're public: `hive/hive`). In prod you'd use a secret-aware JDBC URL or a custom `ConnectionPasswordProvider`.
2. **S3 credentials.** We use `EnvironmentVariableCredentialsProvider` so AWS env vars drive S3A auth. In prod this is IRSA on EKS — pod gets AWS creds from the IAM role, no hardcoded keys anywhere.

### 5. Thrift is the wire protocol

HMS speaks Thrift, not HTTP. It's a binary RPC framework — synchronous, strongly-typed, generated stubs per language. The IDL is [hive_metastore.thrift](https://github.com/apache/hive/blob/master/standalone-metastore/metastore-common/src/main/thrift/hive_metastore.thrift). Key methods:

```
get_databases()                         → list of DB names
get_tables(db, pattern)                 → list of table names matching glob
get_table(db, tbl)                      → full table metadata
get_partitions(db, tbl, max_parts)      → list of partition specs
create_database(db_object)              → insert into DBS
append_partition(db, tbl, values)       → insert into PARTITIONS
```

You almost never write Thrift client code directly — Spark and StarRocks do it for you. But understanding the shape matters when something fails: "why is this Thrift call hanging?" usually reduces to "HMS can't reach Postgres" or "Postgres is slow", not an HMS bug.

### 6. Delta Lake relaxes HMS's role

In the pure-Hive world, HMS is the source of truth for partitions. If HMS says `zoneId=3` exists, Spark reads it; if not, Spark doesn't.

With Delta Lake (which Phase 4 uses), HMS stores only the *table root path*. The `_delta_log/NNN.json` files inside that path are the source of truth for what's live, what's tombstoned, column schema, stats. Delta's writer does keep HMS in sync for discovery (`SHOW PARTITIONS` works), but the read path doesn't trust HMS for correctness — it reads the log.

Net effect: with Delta, HMS is a naming service. With raw Parquet + Hive, HMS is the source of truth. Our lab uses Delta but keeps HMS configured the old way — that's production-faithful, and it's what lets StarRocks discover the tables via its Hive connector.

---

## Setup — step by step (fresh start)

Day 3 continues to follow the "destroy, rebuild, verify each layer" pattern from Day 2. The whole Day 1 + Day 2 + Day 3 chain takes ~20 minutes on a warm cache.

All commands from `days/day-3/` unless noted.

### Step 0 — Destroy previous state (if any)

```bash
make minikube-destroy        # wipes the data-pipeline profile
rm -rf apps/.build           # optional: nuke buildx tarballs
```

### Step 1 — Pre-flight (one-time per laptop)

```bash
make -C apps buildx-init     # only needed if Day 2 was skipped
```

### Step 2 — Bring up Minikube

```bash
make minikube-up
```

### Step 3 — Day 1 foundations: Phase 0, MinIO, Kafka, Schema Registry

```bash
make phase0
make minio && make verify-minio
make kafka && make verify-kafka
make schema-registry && make verify-schema-registry
```

Same targets as Day 2. No changes. See [../day-2/README.md](../day-2/README.md) for details.

### Step 4 — Day 2 apps: Mock GAP + Classroom Simulator

```bash
make apps-build
make apps-deploy
```

Also unchanged from Day 2. After this step you should have events flowing into Kafka — verify with `make -C apps logs-sim`.

### Step 5 — Day 3, Phase 3a: Postgres (HMS backend)

```bash
make postgres
make verify-postgres
```

What this does:

- Applies `postgres-secret.yaml` — DB user/pass/db name (all `hive/hive/metastore` for the lab)
- Applies `postgres-service.yaml` — ClusterIP `hms-postgres:5432` plus a headless Service (for StatefulSet DNS)
- Applies `postgres-statefulset.yaml` — `postgres:16.4`, 10Gi PVC mounted at `/var/lib/postgresql/data`
- Waits for rollout Ready (~30–60s)

`verify-postgres` runs three checks:
1. StatefulSet Ready
2. `psql \l` shows the `metastore` database
3. A one-shot `postgres:16.4` pod connects via the Service and runs `SELECT 1`

If step 3 fails, the Service is misconfigured or the pod's network policy is blocking — check `kubectl describe svc hms-postgres`.

### Step 6 — Day 3, Phase 3b: HMS config + deploy

```bash
make hms-config
make hms-deploy
```

`hms-config` applies:
- `s3a-credentials-secret.yaml` — AWS-style env vars for S3A (minioadmin/minioadmin)
- `hms-configmap.yaml` — the full `hive-site.xml` (see [core concepts §4](#4-hive-sitexml-is-the-one-config))

`hms-deploy` applies:
- `hms-deployment.yaml` — `apache/hive:4.0.0` with `SERVICE_NAME=metastore`, `DB_DRIVER=postgres`, S3A env vars from `s3a-credentials`, `hive-site.xml` mounted from ConfigMap
- `hms-service.yaml` — ClusterIP `hive-metastore:9083` (Thrift)
- Waits for rollout Ready (~90–120s on first run)

The pod does three things at startup:
1. **`initContainer` fetches the Postgres JDBC driver.** `apache/hive:4.0.0` does not bundle it — `schematool` would fail with `ClassNotFoundException: org.postgresql.Driver` otherwise. The initContainer curls `postgresql-42.7.4.jar` into a shared emptyDir.
2. **Main container stages files + runs the image entrypoint.** Copies the jar to `/opt/hive/lib/` and `hive-site.xml` to `/hive-conf/`, then execs `/entrypoint.sh`.
3. **Entrypoint runs `schematool -initOrUpgradeSchema` then launches HMS.** `-initOrUpgradeSchema` is idempotent in Hive 4 — fresh DB gets the schema; already-initialized DB is a no-op. No separate Job needed.

Watch it bring up:
```bash
kubectl -n data-pipeline logs -f deployment/hive-metastore
```

Expected tail once Ready:
```
Initialized schema successfully..
+ exec /opt/hive/bin/hive --skiphadoopversion --skiphbasecp --verbose --service metastore
Starting Hive Metastore Server
Starting hive metastore on port 9083
```

If the pod is `CrashLoopBackOff`, see [failure scenarios](#failure-scenarios-and-how-to-fix-them). The two most likely causes are the JDBC-driver fetch failing (initContainer logs) and the entrypoint hitting a read-only `/opt/hive/lib` (main-container logs show `Permission denied`).

### Step 7 — Verify end-to-end

```bash
make verify-hms
```

Five checks:
1. HMS Deployment rollout Ready
2. Postgres has the HMS tables (`DBS`, `TBLS`, `PARTITIONS` all present)
3. Schema `VERSION` row populated (proves schematool ran)
4. Thrift :9083 is reachable from a sidecar pod
5. `metatool -listFSRoot` round-trips (HMS → Postgres → returns the warehouse path)

### Step 8 — Poke around

Open a psql shell and look at the schema:
```bash
make hms-psql
```
Inside:
```sql
\dt                                           -- ~60 tables
SELECT "NAME", "DB_LOCATION_URI" FROM "DBS";  -- empty at this point
SELECT * FROM "VERSION";                      -- 4.0.0
```

Tail HMS logs:
```bash
make hms-logs
```

Create a database through HMS directly (the pod ships the Hive CLI):
```bash
kubectl -n data-pipeline exec -it deploy/hive-metastore -- \
  /opt/hive/bin/hive -e "CREATE DATABASE IF NOT EXISTS db_deltalake; SHOW DATABASES;"
```

Then watch it appear in Postgres:
```bash
make hms-psql
SELECT "NAME", "DB_LOCATION_URI" FROM "DBS";
```

This is the whole HMS round-trip in miniature. Everything Spark does in Phase 4 is more of the same.

### One-liner (once you're comfortable)

```bash
make minikube-destroy
make up                      # Day 1 + Day 2 apps + Day 3
```

`make up` chains: `minikube-up → phase0 → minio → kafka → schema-registry → apps-up → postgres → hms`. End-to-end ~20 minutes on a warm cache.

### Tearing down

```bash
make minikube-destroy        # recommended — start fresh tomorrow
```

---

## Testing — three layers

### Layer 1 — schema layout in Postgres

The simplest sanity check. If the schema is wrong, nothing HMS does afterward will work.

```bash
make hms-psql
```
```sql
\dt
-- Expect ~60 tables. Key ones: "DBS", "TBLS", "PARTITIONS", "SDS",
-- "COLUMNS_V2", "TABLE_PARAMS", "SERDES", "VERSION".

SELECT * FROM "VERSION";
-- schema version '4.0.0'. If this row is missing, schematool did not run.
```

### Layer 2 — HMS RPC round-trip

Prove that the Thrift server in HMS can actually talk to Postgres and to S3A. `metatool` is the simplest way without needing a Spark session:

```bash
POD=$(kubectl -n data-pipeline get pod -l app=hive-metastore \
        -o jsonpath='{.items[0].metadata.name}')
kubectl -n data-pipeline exec "$POD" -- /opt/hive/bin/metatool -listFSRoot
```

Expected output:
```
Listing FS Roots..
s3a://analytics-lab/warehouse
```

This is the litmus test. If `metatool` can list the FS root, then HMS has:
- Parsed `hive-site.xml` correctly
- Connected to Postgres successfully
- Resolved the S3A filesystem
- Authenticated to MinIO via env-var credentials

If any of those fail, you get a specific exception that maps to a specific fix (see [failure scenarios](#failure-scenarios-and-how-to-fix-them)).

### Layer 3 — CREATE DATABASE via HiveCLI

This actually mutates HMS state:

```bash
kubectl -n data-pipeline exec deploy/hive-metastore -- \
  /opt/hive/bin/hive -e "CREATE DATABASE IF NOT EXISTS db_deltalake;"

kubectl -n data-pipeline exec deploy/hive-metastore -- \
  /opt/hive/bin/hive -e "SHOW DATABASES;"
```

Expected:
```
OK
default
db_deltalake
```

Then verify the row exists in Postgres:
```bash
make hms-psql
SELECT "NAME", "DB_LOCATION_URI" FROM "DBS";
-- default       s3a://analytics-lab/warehouse
-- db_deltalake  s3a://analytics-lab/warehouse/db_deltalake.db
```

If you see both rows, Phase 3 is fully working.

---

## Failure scenarios and how to fix them

### Failure 1 — `cp: cannot create regular file '/opt/hive/lib/postgresql.jar': Permission denied`

**Symptom:** HMS pod `CrashLoopBackOff`. Main-container logs end with the `Permission denied` line above and nothing else.

**Root cause:** `apache/hive:4.0.0`'s Dockerfile sets `USER hive` as the final stage. That user doesn't own `/opt/hive/lib`, so the JDBC driver copy fails.

**Fix (already applied):** pod spec sets `securityContext.runAsUser: 0`. No workload isolation concerns in a lab. In production you'd instead:
- Build a custom image that bakes the JDBC driver in at build time, *or*
- Write the jar to a volume mounted at a writable path and set `HIVE_AUX_JARS_PATH` so `schematool` + metastore pick it up off the default classpath.

**Why this matters:** most "official" OCI images that claim "just run it" embed assumptions about runtime writability. When you're wiring extra jars, certificates, or config in, you either build a derived image (prod-shape) or you run as root (lab-shape). The `HIVE_AUX_JARS_PATH` middle path exists but adds enough complexity that it's rarely worth it.

### Failure 2 — `ClassNotFoundException: org.postgresql.Driver`

**Symptom:** the entrypoint's `schematool -initOrUpgradeSchema` fails; logs contain:
```
Underlying cause: java.lang.ClassNotFoundException : org.postgresql.Driver
Schema initialization failed!
```

**Root cause:** `apache/hive:4.0.0` does *not* bundle the Postgres JDBC driver. It ships Derby only.

**Fix (already applied):** the `fetch-postgres-jdbc` initContainer curls `postgresql-42.7.4.jar` into a shared emptyDir; the main container copies it into `/opt/hive/lib/` before calling the entrypoint.

**Why this matters:** every "generic" DB-backed application has this gap. The JDBC driver licensing usually prevents the base image from bundling every possible backend, so you pick one per deployment. This is the same pattern you'll hit with: Trino → any DB, Flink → CDC sources, Debezium → all of its connectors.

### Failure 3 — `MetaException: Version information not found in metastore`

**Symptom:** HMS pod `CrashLoopBackOff`; logs contain this message.

**Root cause:** `schematool` didn't run (or ran against a different DB). HMS reads `SELECT * FROM "VERSION"` on startup; no row = refuse to start.

**Fix:**
```bash
kubectl -n data-pipeline rollout restart deployment/hive-metastore
```
The restart re-runs the entrypoint, which calls `-initOrUpgradeSchema` again. If Postgres is actually empty, this creates the schema. If it's a different DB, check `javax.jdo.option.ConnectionURL` in the ConfigMap matches the Postgres Service name.

**Why this matters:** `hive.metastore.schema.verification=true` is the prod-safe default. It forces schema changes to be an explicit migration step rather than "the app will fix it on boot". You want this in prod — you do not want HMS auto-upgrading its own schema mid-rollout.

### Failure 4 — `MetaException: … Connection refused (hms-postgres:5432)`

**Symptom:** HMS pod logs show repeated connect failures to Postgres.

**Root cause tree:**

1. **Postgres isn't Ready.** `kubectl get pod -l app=hms-postgres` — if not `1/1`, describe it.
2. **Postgres is Ready but HMS started first.** HMS doesn't wait — it assumes `hive-site.xml` points at a working DB. Restart HMS: `rollout restart deployment/hive-metastore`.
3. **Wrong JDBC URL.** Check `kubectl get cm hive-metastore-site -o yaml | grep ConnectionURL` matches the Service name.

**Why this matters:** startup-order dependencies are a perpetual source of flakiness in K8s. Kubernetes does not guarantee pod A starts before pod B unless you wire `initContainers` or external readiness gates. HMS is resilient to this in the sense that restarting fixes it — but if your CD pipeline deploys HMS before Postgres is Ready, you will see CrashLoopBackOff cycles in your first few minutes.

### Failure 5 — `AccessDenied: 403` from S3A

**Symptom:** `metatool -listFSRoot` prints:
```
ERROR FileSystem: Failed to listObjects: com.amazonaws.services.s3.model.AmazonS3Exception:
  Access Denied (Service: Amazon S3; Status Code: 403; Error Code: AccessDenied)
```

**Root cause tree:**

1. **Env vars not set.** Check `kubectl exec deploy/hive-metastore -- env | grep AWS_` — both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` should be present. If missing, the Secret isn't wired.
2. **Env vars wrong.** If you used different MinIO creds, they must match what `s3a-credentials-secret.yaml` declares.
3. **Endpoint wrong.** `fs.s3a.endpoint` in `hive-site.xml` must be `http://minio:9000`, not `https`, not `minio:9000` (no scheme).
4. **Path style disabled.** `fs.s3a.path.style.access=true` is required for MinIO. Without it, Hadoop tries `http://analytics-lab.minio:9000/` (virtual-host style), which MinIO doesn't serve by default.

**Why this matters:** S3A misconfig is the single most common HMS-with-MinIO failure. Every knob matters. In production the same issues appear differently — IRSA fails silently, bucket policies block the role, endpoint URLs drift between regions — but the debugging shape is identical: `Access Denied` tells you nothing, you have to check each layer individually.

### Failure 6 — HMS Ready but `SHOW TABLES` returns nothing after Spark wrote a table

**Symptom:** (will happen in Phase 4) Spark writes a Delta table. You query HMS from another client and `SHOW TABLES IN db_deltalake` returns empty.

**Root cause tree:**

1. **Spark was configured without `hive.metastore.uris`.** If not set, Spark uses its embedded Derby catalog, not HMS. The table exists in Spark's local state only. Check Spark's `hive-site.xml` contains `<name>hive.metastore.uris</name><value>thrift://hive-metastore:9083</value>`.
2. **`CREATE DATABASE` wasn't called.** Spark's `saveAsTable` will fail if the DB doesn't exist. Create it first (step 9 above).
3. **Wrong DB name.** Spark writes to `default.tbl_analytics_bronze`; you queried `db_deltalake.tbl_analytics_bronze`. Different DB, different row in HMS.

**Why this matters:** this is *the* catalog-drift class of failure. The exact shape varies (external catalog caching in StarRocks, partition pruning in Spark, etc.) but the diagnostic question is always: "where did the metadata go, and what did I expect to find it?"

### Failure 7 — Thrift :9083 open but clients hang

**Symptom:** `nc -zv hive-metastore 9083` says open. Spark/StarRocks/`hive` CLI hang on every call.

**Root cause:** Thrift server thread pool exhausted. HMS defaults to `metastore.server.min.threads=200`, `metastore.server.max.threads=1000`. A connection leak (clients not closing Thrift sessions) eats those up. In a lab you won't see this — in prod with thousands of Spark executors you will.

**Fix:** increase thread limits in `hive-site.xml`, or fix the leaking client.

**Why this matters:** Thrift connection management is an old-world concern (2010s-era RPC). Every Thrift deployment I've seen has eventually hit this. Worth knowing the failure mode exists.

---

## Points to ponder

Sit with these. They map to failure modes you'll actually diagnose in real systems.

1. **If you drop the Postgres StatefulSet's PVC, what breaks?** (Answer: every table definition is gone. Actual S3 files survive. You have data with no catalog. Recovery: `schematool -initSchema` from scratch, then `MSCK REPAIR TABLE` for every table — assuming you remembered their original `CREATE TABLE` statements and partition column names. This is why HMS backup is non-optional in prod.)

2. **Why does HMS use Thrift instead of HTTP/JSON?** (Answer: HMS predates every modern JSON catalog by a decade. Thrift was what you used when you needed strongly-typed RPC in the JVM ecosystem. Today's reimplementations — Polaris, Unity, Nessie — all use HTTP/REST. Thrift is legacy we're stuck with.)

3. **What happens if you change `metastore.warehouse.dir` from `s3a://analytics-lab/warehouse` to `s3a://analytics-lab/newwarehouse` after writing a table?** (Answer: existing `DB_LOCATION_URI` entries in `DBS` are frozen at their creation time — they don't auto-update. New DBs get the new warehouse. Existing tables still read from the old path. Moving data requires `ALTER DATABASE … SET LOCATION` + actually copying files.)

4. **HMS has two schema-verification knobs: `hive.metastore.schema.verification` and `datanucleus.schema.autoCreateAll`. They're related but not redundant. What does each actually do?** (Hint: one gates startup, the other gates runtime DDL.)

5. **Why do we deploy HMS as a `Deployment` but Postgres as a `StatefulSet`?** (Answer: HMS is stateless — all its state lives in Postgres. A new HMS pod can replace an old one without data loss. Postgres is stateful — the pod identity and the PVC must stay bound. `StatefulSet` guarantees stable pod names (`hms-postgres-0`) and pairs each replica with its own PVC.)

6. **Look at `\dt` in psql — which of the ~60 HMS tables are actually populated after you've done one `CREATE DATABASE` and nothing else?** (Answer: `DBS` has a row. `DATABASE_PARAMS` may have rows if defaults were recorded. Most other tables stay empty until you `CREATE TABLE`. Worth seeing firsthand — it teaches you which tables to check first when debugging.)

7. **Why is the HMS schema upgrade path version-sensitive?** (Hint: schematool migrations are SQL scripts per version jump. 3.1 → 4.0 and 2.3 → 4.0 are different scripts. Skipping versions = running scripts in wrong order = corrupt schema.)

---

## Key takeaways — distilled

If you remember only these:

1. **Catalog stores metadata only.** Tables live in storage; HMS stores where they are, what shape they have, what partitions exist. It is not involved in bytes.
2. **HMS is synchronous and pull-based.** No push, no events, no listeners. Clients ask; HMS answers. Staleness is the caller's responsibility.
3. **schematool is your migration runner.** Explicit, non-idempotent, version-sensitive. `-initSchema` once ever; `-upgradeSchema` for every bump.
4. **`hive-site.xml` is the one config file.** No env overrides worth relying on. Keep credentials out — use env-var providers for S3A, secret-aware JDBC URLs for DB.
5. **Thrift :9083 is the public API.** TCP-level health checks prove the listener; `metatool -listFSRoot` proves the full stack (HMS + Postgres + S3A).
6. **Postgres is the real state; HMS is stateless.** Deploy HMS horizontally (behind a Service), Postgres vertically (one StatefulSet, careful backups).
7. **Delta relaxes HMS's role.** In the lakehouse, HMS is mostly a naming service; the `_delta_log/` is the partition source of truth. With legacy Parquet + Hive, HMS *is* the source of truth — different failure modes.
8. **`MSCK REPAIR TABLE` is your reconciliation tool.** When catalog and storage drift, listing-diff from storage back into HMS. Know it exists.

---

## Red flags — don't move to Day 4 until

- You can draw the three-box diagram (compute ↔ HMS ↔ Postgres, compute ↔ storage) from memory and explain which arrow is which protocol.
- You know what `schematool -initSchema` does and why `hive.metastore.schema.verification=true` forces you to run it explicitly.
- You can open `psql` and identify at least four HMS tables by name and what they hold.
- `hive-site.xml`'s S3A properties are not a black box — you can explain `path.style.access`, `EnvironmentVariableCredentialsProvider`, and what `fs.s3a.endpoint` is doing.
- You can trace an HMS Thrift call through to a Postgres query (logical chain, not code walk).
- You understand why we split Postgres (StatefulSet) and HMS (Deployment).
- You know what `MSCK REPAIR TABLE` does and when you'd need it.
- You can describe the difference between HMS's role in pure-Hive (source of truth for partitions) vs Delta Lake (naming service; log is source of truth).

If any of these feel fuzzy: re-read [../../docs/hive-metastore.md](../../docs/hive-metastore.md) and run the "points to ponder" question it maps to.

---

## What's next — Day 4 teaser

Day 4 adds the **compute layer** — Spark Operator + four Structured Streaming jobs. Kafka events from Day 2 finally get consumed, written as Delta tables to MinIO, and registered in the HMS you stood up today. You'll learn:

- `SparkApplication` CR lifecycle and why operators beat `spark-submit`
- Structured Streaming: micro-batches, watermarks, stateful operators
- Checkpointing — the most important concept in the whole pipeline
- Why "exactly-once" is a property of `(source + sink + checkpoint)`, not Spark alone
- The Delta `_delta_log/` format and how `_delta_log/000.json` actually commits

Phase 4 is the longest phase in the lab (~6 hours focused) and the highest-density set of learnings. HMS will suddenly get interesting once Delta starts writing to it.

---

## Reference — layout

```
day-3/
├── README.md                           this file
├── Makefile                            Day 1+2 targets (carried forward) + phase 3 targets
├── minikube/                           cluster lifecycle (unchanged)
├── k8s/
│   ├── 00-namespace/                   unchanged
│   ├── 01-minio/                       unchanged
│   ├── 02-kafka/                       unchanged
│   └── 03-postgres-hms/                ← NEW today
│       ├── README.md                   phase-3 apply order + prod mapping
│       ├── postgres-secret.yaml
│       ├── postgres-service.yaml       ClusterIP + headless
│       ├── postgres-statefulset.yaml   postgres:16.4, 10Gi PVC
│       ├── s3a-credentials-secret.yaml AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
│       ├── hms-configmap.yaml          hive-site.xml (JDBC + S3A + Thrift)
│       ├── hms-deployment.yaml         apache/hive:4.0.0, SERVICE_NAME=metastore,
│       │                               initContainer fetches Postgres JDBC jar,
│       │                               entrypoint runs -initOrUpgradeSchema
│       └── hms-service.yaml            ClusterIP :9083 Thrift
├── scripts/
│   ├── verify-phase0.sh                unchanged
│   ├── verify-minio.sh                 unchanged
│   ├── verify-kafka.sh                 unchanged
│   ├── verify-schema-registry.sh       unchanged
│   ├── verify-postgres.sh              ← NEW today
│   └── verify-hms.sh                   ← NEW today
└── apps/                               unchanged from Day 2 (mock-gap + classroom-simulator)
```

Deep dive: [../../docs/hive-metastore.md](../../docs/hive-metastore.md).
