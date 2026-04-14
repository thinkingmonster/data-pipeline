# Day 2 — Event Source: Mock GAP + Classroom Simulator

**Today you build the ingress layer of the pipeline.** Two Python services that together turn user activity into a durable, schema-validated, Kafka-resident event stream. This is the first day "data" exists as bytes on disk.

Today's work covers **Phase 2** from [../../LEARNING-ROADMAP.md](../../LEARNING-ROADMAP.md). It depends on Day 1 (Phase 0 + Phase 1 — Minikube, Kafka, Schema Registry, MinIO).

---

## TL;DR

You'll:

1. Stand up two services in the `data-pipeline` namespace:
   - **Mock GAP** — FastAPI ingest gateway. Accepts JSON, validates, serializes to Confluent Avro wire format, produces to Kafka.
   - **Classroom Simulator** — async Python client. Spawns N concurrent meetings, emits realistic events through Mock GAP.
2. Watch three topics (`analytics`, `engagement-topic`, `webrtc-analytics`) fill with real wire-format bytes.
3. Decode those bytes, round-trip them through Schema Registry, confirm they match what was sent.
4. Break the system in specific ways and reason about the recovery.

Time budget: 3–4 hours focused, more if you go deep on the "points to ponder" questions (you should).

---

## Prerequisites

This lab starts **fresh every day** — we destroy the previous state so stale registry subjects, leftover topics, and half-terminated pods can't confuse the next run. All you need locally is:

- `minikube` (≥ 1.33), `kubectl`, `docker` (with `buildx`), `make`, `curl`, Python 3.12
- ~30 GB free disk (the `data-pipeline` profile is sized for the full stack)
- ~20 minutes on a warm cache for the full Day 1 + Day 2 bring-up

Run all commands from `days/day-2/` unless noted otherwise.

---

## Architecture — the Phase 2 slice

```
 ┌─────────────────────────┐   asyncio + Poisson arrivals
 │  classroom-simulator    │   N meetings in parallel, 5 zones
 │  (Python / httpx)       │   zone weights = 50/25/15/7/3 %
 └────────────┬────────────┘
              │  POST /api/gap/ingest  (JSON)
              ▼
 ┌─────────────────────────────────────────────────────┐
 │  mock-gap  (FastAPI + aiokafka)                     │
 │                                                     │
 │   1. route eventType → topic  (analytics | ...)     │
 │   2. look up / register schema in Schema Registry   │
 │      (cached by subject)                            │
 │   3. encode Confluent wire format:                  │
 │        [0x00][4B schema_id BE][Avro bytes]          │
 │   4. produce to Kafka (acks=all, idempotent,        │
 │      key = meetingId, linger.ms = 10)               │
 └─────────────┬───────────────────────────────────────┘
               │  Kafka protocol (plaintext)
               ▼
 ┌─────────────────────────────────────────────────────┐
 │  Strimzi Kafka (KRaft, 3 brokers, 3 controllers)    │
 │   ├─ analytics          p=3  keyed by meetingId     │
 │   ├─ engagement-topic   p=3  keyed by meetingId     │
 │   ├─ webrtc-analytics   p=3  keyed by meetingId     │
 │   └─ _schemas           p=1  (SR backing, compact)  │
 └─────────────────────────────────────────────────────┘
```

Nothing consumes yet. That's Phase 4 (Spark). Today you're verifying the log, not reading it.

---

## What you'll have when you're done

- Two Deployments healthy in `data-pipeline` namespace
- Three topics accumulating valid Avro-encoded messages, keyed by `meetingId`
- Three Schema Registry subjects (`<topic>-value`)
- Prometheus metrics on `mock-gap:8000/metrics` — send rates, produce latency histograms, SR hit ratios
- A working mental model of producer semantics, wire format, and schema evolution

---

## Core concepts you need to internalize

Read each of these twice. The goal is not to memorize — it's to feel the trade-off.

### 1. Kafka is a replicated log, not a queue

Messages are *not* deleted on read. Consumers track their own offsets. You can have ten different consumers at ten different offsets on the same partition, all reading at their own pace, all seeing the same bytes. This is why "replay" is a first-class concept in Kafka and an afterthought in RabbitMQ.

**Why it matters today:** our Spark job in Phase 4 will read from `earliest` on first run, then resume from its checkpointed offset on restart. The events you produce today aren't going anywhere.

### 2. Partition key = ordering contract

Kafka guarantees ordering **per partition**, not per topic. If two events must be processed in order, they *must* land on the same partition. The only reliable way to guarantee that: same partition key.

We key by `meetingId`. That means:
- All events for meeting `m-abc123` are strictly ordered
- Events across meetings can be processed in parallel by multiple consumers

**Picking the key is a commitment.** You cannot change it later without data migration, because existing data is already distributed by the old key.

Alternatives:
- `userId` — ordered per user, not per meeting (wrong for us — two teachers emitting events in the same meeting could race)
- no key (round-robin) — best throughput, zero ordering guarantees
- `zoneId` — only 5 distinct values → massive partition skew, bad parallelism

### 3. `acks` trade-off

| Setting     | Semantics                                | Durability      | Latency |
|-------------|------------------------------------------|-----------------|---------|
| `acks=0`    | fire-and-forget (producer doesn't wait)  | lose on broker crash | lowest |
| `acks=1`    | leader confirms write                    | lose if leader dies before replication | mid |
| `acks=all`  | leader + `min.insync.replicas` confirm   | survives N−1 broker failures | highest |

We use `acks=all` with `min.insync.replicas=2` and `replication.factor=3`. That means a message is considered written only when 2 of the 3 brokers have it. Loses data only if 2 brokers die simultaneously before replication completes.

### 4. Idempotent producer ≠ exactly-once

`enable_idempotence=True` means: the producer attaches a `(producer_id, sequence_number)` to every record. If the broker sees a duplicate sequence for the same producer, it silently drops it.

**What this protects:** network retries within one producer session. The producer sends record #42, the ACK is lost, the producer retries, the broker de-dups. Zero duplicates.

**What this does NOT protect:** client-level retries (two *different* `POST /api/gap/ingest` requests with the same body = two different records). Pod restarts (new producer_id = no de-dup history). Any application-layer retry that builds a fresh producer.

Exactly-once across restarts requires Kafka **transactions** — we deliberately don't use them here. Exactly-once at the pipeline level will come from the Spark + Delta + checkpoint triple in Phase 4.

### 5. `linger.ms` + `batch.size` — the throughput knob

By default, aiokafka sends each record as its own batch. That's one TCP write per record. Terrible for throughput.

`linger.ms=10` tells the producer: wait up to 10ms, collect records going to the same partition, send them as one batch. You pay at most 10ms of latency, gain 10–100× throughput.

**Trade-off:** lower `linger.ms` = snappier tail latency, lower throughput. Tuning this is an ops job, not a code job.

### 6. Confluent wire format is non-negotiable

Every message on `<topic>-value` starts with exactly 5 bytes:

```
byte 0      : 0x00                   magic byte (schema version — only 0 exists today)
bytes 1–4   : uint32 big-endian      Schema Registry ID
bytes 5+    : Avro binary            the actual payload, per the referenced schema
```

**Why it matters:** Spark's `from_avro()` in Phase 4 does *not* understand these 5 bytes. You must strip them (`expr("substring(value, 6, length(value) - 5)")`) before passing to the Avro reader. Every team learns this the painful way once.

### 7. Schema ID caching

Without caching, every produced record round-trips to Schema Registry:
- 10,000 eps × 1 SR call per event = 10,000 HTTP requests/sec against a single-pod SR

With caching (cache by subject → schema_id), you make **one** SR call the first time you see a subject, then reuse the ID forever (or until the schema evolves). Steady-state SR load is zero.

**Production Kafka clients** (Confluent Java, librdkafka) do exactly this. Our `SchemaCache` class in [apps/mock-gap/app/producer.py](apps/mock-gap/app/producer.py) mirrors that behavior.

### 8. GAP is a translation boundary

Before GAP: JSON, loosely typed, whatever the client sent.

After GAP: Avro binary, strongly typed, conforms to a registered schema.

This is the **single** place validation happens. If you let a malformed event past GAP, it lands in Kafka, Spark picks it up, Delta writes it, and now you have corrupt bytes with a valid schema_id prefix in an ACID-committed Parquet file. The only fix is a time-travel revert. Don't do this.

---

## Setup — step by step (fresh start)

**Run these top to bottom.** Each day starts by destroying the previous profile — stale Schema Registry subjects, leftover topic data, half-crashed pods, and partial deploys from yesterday all silently break today's runs. Starting fresh is cheap (~15 min) and saves hours of "why is this 409-ing" debugging.

All commands run from `days/day-2/` unless noted.

### Step 0 — Destroy whatever's left from previous runs

```bash
make minikube-destroy          # wipes the data-pipeline profile entirely
# it's safe to run even if nothing exists — it reports "not found" and moves on

# (optional) nuke the OCI tarballs buildx wrote locally
rm -rf apps/.build
```

What this removes: minikube VM, all PVCs, all pods, the Strimzi operator, the Kafka cluster, Schema Registry, MinIO, bucket data, the `_schemas` compacted topic. Your `docker buildx` builder survives — that's a laptop-level resource, not minikube-scoped.

### Step 1 — Pre-flight (one-time per laptop)

You only need to do this once, *ever*, on a given machine — not per day:

```bash
make -C apps buildx-init
```

First time pulls `moby/buildkit:buildx-stable-1` (~30s). Verify:

```bash
docker buildx ls | grep lab13-builder
```

### Step 2 — Bring up minikube (Phase 0 prerequisite)

```bash
make minikube-up
```

Starts the `data-pipeline` profile with 10 CPU / 24 GB RAM / containerd runtime. Takes 60–90 seconds. Verify:

```bash
kubectl --context=data-pipeline get nodes
# expect 1 node, status Ready
```

### Step 3 — Day 1, Phase 0: namespace + ResourceQuota

```bash
make phase0
make verify-phase0
```

What you get: `data-pipeline` namespace with a ResourceQuota that admission-controls every later phase. Verify script intentionally schedules a pod over the quota and confirms it's rejected — that's the proof the admission controller is live.

### Step 4 — Day 1, Phase 1a: MinIO (S3-compatible storage)

```bash
make minio
make verify-minio
```

What you get: a single-node MinIO StatefulSet with bucket `analytics-lab` pre-created by an init Job. No Spark / Delta yet, but the bucket needs to exist by the time Phase 4 starts writing to it.

### Step 5 — Day 1, Phase 1b: Strimzi Kafka (KRaft, 3 brokers)

```bash
make kafka
make verify-kafka
```

This is the longest step: **3–5 minutes**. The Strimzi operator installs, then provisions 3 controllers (KRaft quorum) + 3 brokers, then declares 4 `KafkaTopic` CRs. The Makefile waits for `kafka/data-pipeline` to report `Ready=True` before exiting.

If `make kafka` seems hung, watch progress in another shell:
```bash
kubectl --context=data-pipeline -n data-pipeline get pods -w
```

Verify script produces + consumes a round-trip message against the `analytics` topic. **Note:** this writes a plain-text `day-1-smoke` payload into partition 0 — that's why our Day 2 decoder skips non-wire-format messages (see [Failure 3](#failure-3--day-1-leftover-bad-magic-0x64-when-decoding)).

### Step 6 — Day 1, Phase 1c: Confluent Schema Registry

```bash
make schema-registry
make verify-schema-registry
```

Deploys SR (backed by the `_schemas` compacted topic on Kafka), registers a toy schema under `analytics-value`, fetches it back by ID.

**Leave the toy schema in place** — our Day 2 mock-gap will try to register a more complete schema and fail with a 409 BACKWARD-compat error. That's not a mistake; it's [Failure 2](#failure-2--schema-being-registered-is-incompatible-with-an-earlier-schema-http-409) and it's one of today's learning moments. If you want to skip the learning and just make things work, delete the subject after this step (two commands — see "Why two steps" below):

```bash
# soft delete (marks subject deleted, data still in _schemas topic)
kubectl --context=data-pipeline -n data-pipeline exec deploy/schema-registry -- \
  curl -sS -X DELETE "http://localhost:8081/subjects/analytics-value"

# hard delete (requires soft delete first — this is a Registry safety feature)
kubectl --context=data-pipeline -n data-pipeline exec deploy/schema-registry -- \
  curl -sS -X DELETE "http://localhost:8081/subjects/analytics-value?permanent=true"
```

If you try hard delete first you'll get `error_code 40405: "Subject 'analytics-value' was not deleted first before being permanently deleted"`. That's Schema Registry's "Type 'delete' to confirm" — soft delete is the staging step, hard delete is the commit.

### Step 7 — Checkpoint: confirm Day 1 is healthy

```bash
kubectl --context=data-pipeline -n data-pipeline get pods
```

You should see 11 pods, all `1/1 Running` (no restarts, no `CrashLoopBackOff`):
- `data-pipeline-broker-{0,1,2}`
- `data-pipeline-controller-{3,4,5}`
- `data-pipeline-entity-operator-*` (`2/2`)
- `minio-0`
- `minio-bucket-init-*` (`0/1 Completed` — that's a finished Job, not a failure)
- `schema-registry-*`
- `strimzi-cluster-operator-*`

If any are flapping, go to [Failure 5](#failure-5--simulator-reports-all-502s-no-200s) and work bottom-up.

### Step 8 — Day 2, Phase 2a: build both images multi-arch

```bash
make apps-build
```

Per image, this runs:
- `docker buildx build --platform linux/amd64,linux/arm64` — produces a manifest list
- Exports to an OCI tarball (`--load` doesn't support multi-arch builds)
- `minikube image load` — extracts the arch matching the minikube node

~60–90 seconds total on a warm cache. Verify:

```bash
minikube --profile data-pipeline image ls | grep -E "mock-gap|classroom-simulator"
# expect:
#   docker.io/library/mock-gap:day2
#   docker.io/library/classroom-simulator:day2
```

### Step 9 — Day 2, Phase 2b: deploy mock-gap + simulator

```bash
make apps-deploy
```

Both Deployments should be `1/1 Ready` in ~30 seconds. If not, jump straight to [Failure scenarios](#failure-scenarios-and-how-to-fix-them).

### Step 10 — Watch it flow

Two terminals:

```bash
# terminal 1 — simulator stats (every 10s)
make -C apps logs-sim

# terminal 2 — GAP request logs
make -C apps logs-gap
```

Expected simulator output:
```
INFO simulator - meeting started id=m-7292f72b10 zone=1 students=5
INFO simulator - stats sent=96 failed=0 rate_eps=9.6 elapsed=10.0s
INFO simulator - stats sent=193 failed=0 rate_eps=9.7 elapsed=20.0s
```

Expected GAP output (intermixed httpx + uvicorn):
```
INFO app.producer - registered subject=analytics-value schema_id=4
INFO:     10.244.0.18:32928 - "POST /api/gap/ingest HTTP/1.1" 200 OK
```

### One-liner (once you're comfortable with the phases)

After you've done the fresh-start flow once *by hand* and understand what each phase gives you, you can chain it all with a single target. **Don't use this the first time.** You learn nothing from watching a Makefile succeed.

```bash
make minikube-destroy        # start clean
make up                      # minikube + phase 0 + phase 1 + apps
```

`make up` is defined in this directory's Makefile as `minikube-up phase0 minio kafka schema-registry apps-up`. End-to-end it takes 12–18 minutes on a warm cache (Kafka Ready-wait is the long pole).

### Fast iteration loop (when you're editing app code)

The `buildx` path is ~45s per image. When you're tweaking `mock-gap/app/*.py` tightly and just want to see the effect:

```bash
make -C apps build-fast    # single-arch, built directly in minikube's containerd
make -C apps restart       # rollout restart (image tag is reused)
```

That drops the cycle to ~10s. Don't ship with `build-fast` — `make apps-build` before committing so the image is multi-arch.

### Tearing down at end of day

Two options, pick based on tomorrow:

```bash
make minikube-stop       # keep state; `minikube start` resumes in 60s
make minikube-destroy    # wipe everything; tomorrow you start fresh (recommended)
```

This lab is designed around `destroy` — see the top of [Setup](#setup--step-by-step-fresh-start) for why.

---

## Testing — three layers

The cheapest bug to find is the one that never leaves your laptop. Work outward:

### Layer 1 — offline logic test (no Kafka, no HTTP)

```bash
cd apps
python3 -m venv .venv
.venv/bin/pip install -r mock-gap/requirements.txt -r classroom-simulator/requirements.txt
.venv/bin/python scripts/test_event_shapes.py
```

This validates:
- Every simulator event routes to a known `eventType`
- Every event validates against its target Avro schema (via `fastavro.validate`)
- Wire-format `encode()` → parse header → Avro decode round-trips cleanly
- Zone distribution (10k trials) matches configured weights within 2%

All four checks must print `OK`. **This caught the `aiokafka[lz4]` dependency bug on the first pass.** If logic is wrong here, no amount of in-cluster debugging will save you — fix it here first.

### Layer 2 — in-cluster decode (the real thing)

The simulator's own logs tell you requests are succeeding. But "200 OK from GAP" only means GAP *accepted* the event — to prove it landed correctly, decode the actual bytes from Kafka.

From inside the cluster (avoids the Strimzi internal-listener problem — see failures below):

```bash
# copy decoder into the running mock-gap pod (which has fastavro + aiokafka already)
POD=$(kubectl --context=data-pipeline -n data-pipeline get pod -l app=mock-gap \
        -o jsonpath='{.items[0].metadata.name}')
kubectl --context=data-pipeline -n data-pipeline cp apps/scripts/decode_samples.py $POD:/tmp/decode_samples.py
kubectl --context=data-pipeline -n data-pipeline exec $POD -- python /tmp/decode_samples.py
```

Expected (sample):
```
=== analytics ===
  [p=1 off=0 key='m-9e1adea5de' sid=4 61B]
    {"eventType":"user_join","meetingId":"m-9e1adea5de","payload":{"role":"student"},"timestamp":1776183753951,"userId":"s-d7814f4f","zoneId":3}
  [p=1 off=1 key='m-9e1adea5de' sid=4 62B]
    {"eventType":"user_leave","meetingId":"m-9e1adea5de","payload":{"role":"student"},"timestamp":1776183755198,"userId":"s-ffa25621","zoneId":3}
```

Things to verify in the output:
- `key` equals `meetingId` (partition-key contract holds)
- `zoneId` camelCase on analytics/webrtc, `zone_id` snake_case on engagement (the production-faithful casing quirk)
- `sid` is stable per subject (no schema thrash)
- `p=` varies across runs as `meetingId` hashes differently (but same key always goes to same partition)

### Layer 3 — observability

Scrape `/metrics`:

```bash
POD=$(kubectl --context=data-pipeline -n data-pipeline get pod -l app=mock-gap \
        -o jsonpath='{.items[0].metadata.name}')
kubectl --context=data-pipeline -n data-pipeline exec $POD -- \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8000/metrics").read().decode())' \
  | grep -E '^gap_' | head -30
```

Interpret:
- `gap_records_sent_total{topic,event_type}` — Counter. Rate of change = current throughput.
- `gap_records_failed_total{topic,reason}` — should be 0. If non-zero, check `reason` label.
- `gap_produce_latency_seconds_bucket{topic,le}` — latency histogram. `le=0.01` count vs total = fraction under 10ms.
- `gap_schema_registry_hits_total{result}` — should be `cache_hit` >> `registered`. `lookup_fail` must be 0.

---

## Failure scenarios and how to fix them

These are not hypotheticals. The first two **actually happened** during my dry-run of this lab. You'll hit them too if you rebuild from scratch. Knowing the shape of the error is 80% of the diagnosis.

### Failure 1 — `RuntimeError: Compression library for lz4 not found`

**Symptom:** `mock-gap` pod logs show this on startup, then `CrashLoopBackOff`.

```
File ".../aiokafka/producer/producer.py", line 248, in __init__
    raise RuntimeError(...)
RuntimeError: Compression library for lz4 not found
```

**Root cause:** the `lz4` PyPI package is *not* the compression backend aiokafka checks for. aiokafka wants `cramjam` (pulled in by `aiokafka[lz4]` extra).

**Fix:** change `requirements.txt`:

```diff
- aiokafka==0.12.0
- lz4==4.3.3
+ aiokafka[lz4]==0.12.0
```

**Why this matters as a pattern:** always use the library's own "extras" for optional codecs/SASL/TLS/etc. Don't guess the underlying PyPI package name. `pip install 'aiokafka[lz4,snappy,zstd,gssapi]'` is the right shape.

### Failure 2 — `Schema being registered is incompatible with an earlier schema` (HTTP 409)

**Symptom:** GAP returns `502 Bad Gateway`, logs show:

```
schema registry returned 409 for analytics-value: ... 'eventType' ... has no default value
and is missing in the old schema ... {oldSchemaVersion: 1} ...
{validateFields: 'false', compatibility: 'BACKWARD'}
```

**Root cause:** something registered an older, incompatible schema under `analytics-value` (typically a Day 1 verify script's toy schema with only `{meetingId, zoneId, ts}`). Schema Registry is in `BACKWARD` compat mode, which means the *new* schema must be readable by the *old* consumer. Adding required fields (no defaults) breaks that.

**Fix (lab-safe — destroys history):**

```bash
kubectl --context=data-pipeline -n data-pipeline exec deploy/schema-registry -- \
  curl -sS -X DELETE http://localhost:8081/subjects/analytics-value
kubectl --context=data-pipeline -n data-pipeline exec deploy/schema-registry -- \
  curl -sS -X DELETE "http://localhost:8081/subjects/analytics-value?permanent=true"
kubectl --context=data-pipeline -n data-pipeline rollout restart deployment/mock-gap
```

(Soft delete first — moves it to "tombstoned" state. Hard delete with `?permanent=true` — removes it entirely. In prod you'd never hard-delete without a migration plan.)

**Fix (prod-safe):** either (a) evolve the new schema to be BACKWARD-compatible with the old one (add defaults for every new field), or (b) register the new schema under a *different* subject name. Never hard-delete in prod — you'll break every consumer that has the old schema ID cached.

**Why this matters as a pattern:** compatibility modes are **upgrade-ordering contracts**:

| Mode        | New schema must be readable by | Change order              |
|-------------|--------------------------------|---------------------------|
| `BACKWARD`  | old consumers                  | upgrade consumers first   |
| `FORWARD`   | new consumers reading old data | upgrade producers first   |
| `FULL`      | both                           | either order              |
| `NONE`      | nobody — YOLO                  | don't use in prod         |

Pick the mode that matches how you deploy. Lab is `BACKWARD` by default — we upgrade consumers (Spark) *after* producers (GAP).

### Failure 3 — Day 1 leftover: `BAD MAGIC: 0x64` when decoding

**Symptom:** your decoder reports that the very first message in a partition has magic byte `0x64` ('d'), not `0x00`.

**Root cause:** Day 1's `verify-kafka.sh` used `kafka-console-producer` to smoke-test the cluster. That tool sends plain UTF-8 text, not Avro. The first message in `analytics` is literally `day-1-smoke`.

**Fix:** nothing to fix — it's historical noise. Your decoder should tolerate it:

```python
if raw[0] != 0x00:
    # non-wire-format leftover; skip it
    continue
```

**Why this matters as a pattern:** in production you will get garbage in topics. Consumers must defend themselves. "The producer promised to use wire format" is never good enough — topic retention is weeks, a rogue writer earlier will haunt you forever. Always validate the magic byte.

### Failure 4 — `KafkaConnectionError: No connection to node with id 2` (running GAP locally)

**Symptom:** trying to run `mock-gap` on your laptop against a port-forwarded Kafka fails the second anything tries to produce.

**Root cause:** Strimzi's `plain` listener is `type: internal`. It advertises broker hostnames like `data-pipeline-broker-2.data-pipeline-kafka-brokers.data-pipeline.svc` — resolvable only from inside the cluster. Your laptop can reach the bootstrap service via port-forward, but then Kafka hands you a hostname you can't resolve.

**Fix:** don't try. Run GAP inside the cluster (which is where it's going to live anyway). If you genuinely need to produce from outside, add a NodePort or LoadBalancer listener to [days/day-2/k8s/02-kafka/kafka-cluster.yaml](k8s/02-kafka/kafka-cluster.yaml):

```yaml
listeners:
  - name: plain
    port: 9092
    type: internal
    tls: false
  - name: external
    port: 9094
    type: nodeport
    tls: false
```

**Why this matters as a pattern:** Kafka listeners are a *two-step* resolution:

1. Client connects to the bootstrap server (usually a Service IP)
2. Bootstrap replies with a topology: "here are the brokers, here are their advertised addresses"
3. Client then connects directly to those advertised addresses

Step 3 is what fails across network boundaries. MSK, Confluent Cloud, Strimzi — all have the same gotcha. "Can I ping the broker?" isn't the right question; "can I resolve and connect to the *advertised* name?" is.

### Failure 5 — Simulator reports all 502s, no 200s

**Symptom:** `classroom-simulator` logs show `HTTP/1.1 502 Bad Gateway` for every request. No events in Kafka.

**Root cause tree** (check in order):

1. **GAP pod isn't Ready.** `kubectl get pod -l app=mock-gap`. If it's not `1/1`, describe it: `kubectl describe pod ...`. Common reasons: image pull fail, crashed on startup (see Failures 1–2), probe failing.
2. **GAP can't reach Kafka.** Check GAP logs for `KafkaConnectionError`. If Kafka brokers aren't Ready yet, GAP started too early; just `rollout restart deployment/mock-gap`.
3. **GAP can't reach Schema Registry.** Check logs for `httpx.ConnectError` against `schema-registry:8081`. If SR pod is `CrashLoopBackOff`, Kafka isn't Ready yet — SR depends on `_schemas` topic being writable.

**Why this matters as a pattern:** always check dependencies *bottom-up* in distributed systems. Layer N can't be healthy if layer N−1 is flapping. Our stack (bottom to top): Kafka brokers → Schema Registry → GAP → Simulator.

### Failure 6 — p0 has 0 messages, all traffic hits p1 and p2

**Symptom:** `kafka-get-offsets` shows `analytics:0:0` while p1 and p2 have hundreds of messages.

**Root cause:** not a bug. Partition is `hash(key) mod num_partitions`. With 3 concurrent meetings (3 unique keys) and 3 partitions, hash collisions are expected — the odds that all three happen to miss p0 are about 1-in-7. Restart the simulator, you'll see different partitions.

**Why this matters as a pattern:** **small cardinality + few partitions = visible skew.** In prod, with thousands of meetings and hundreds of partitions, skew evens out. In a lab, you see the rough edges. If you ever see real prod skew (one partition 100× another), the investigation is: how many unique keys are there, relative to partition count?

---

## Points to ponder

These don't have quick answers. Sit with each for a bit.

1. **If the GAP pod dies mid-batch with 50 queued records in the aiokafka producer buffer, what happens to those records?** (Hint: read the aiokafka source for `AIOKafkaProducer.stop`, specifically `flush()`. Does the shutdown drain cleanly, or is there a window?)

2. **You run two `mock-gap` replicas behind a Service. The simulator POSTs the same event body twice (same `meetingId`, same `timestamp`). Will they deduplicate?** (Think carefully: idempotent producer state is per-*producer-instance*. Two replicas = two producer IDs. Same sequence numbers on different PIDs = broker treats them as distinct.)

3. **`linger.ms=10` and `batch.size=32KB`. You're sending 200-byte records at 10 eps. Is batching helping you at all?** (Back-of-envelope: 10 eps × 200B = 2KB/sec. A full batch would take 16 seconds to fill. You're bounded by linger, not by size. Tuning `batch.size` up from here buys you nothing.)

4. **You decide to add a new field `sessionId` to the analytics schema. The simulator starts sending it. Old consumers (Spark with a cached old schema) read an event with schema_id=5 (new) but have schema_id=4 cached. Walk through what they see.** (Hint: Confluent deserializers fetch the new schema by ID, project it into the *reader's* schema. If the reader uses BACKWARD-compatible schema and `sessionId` has a default, it's ignored. Without a default, the consumer crashes.)

5. **Why does Schema Registry use a Kafka topic (`_schemas`, compacted) as its source of truth rather than a database?** (Hint: what's the availability profile? What happens on SR restart? What if SR dies and comes back on a different pod — does it need external state?)

6. **The `engagement-topic` schema uses `zone_id` (snake_case) while `analytics` uses `zoneId` (camelCase).** Is this a bug, a learning point, or both? What problem does it create downstream, and what would it take to fix it after the fact?

7. **Strimzi runs 3 controllers *and* 3 brokers as separate pods (KRaft mode).** What does the controller actually do? Why can't one broker be its own controller in a 1-broker cluster? (Look up Raft quorum math.)

---

## Key takeaways — distilled

If you remember only these:

1. **Ordering is a partition-key property, not a topic property.** Pick the key when you create the topic; changing it later means migration.
2. **Idempotent producer is a session-local guarantee.** Exactly-once end-to-end requires the consumer to also participate (checkpoints, transactions, or idempotent sinks).
3. **Schema Registry IDs are a cache-once-use-forever contract.** Cache aggressively; the Registry is not your hot path.
4. **Compatibility mode determines deploy order.** BACKWARD = upgrade consumers first. Get this wrong and you'll know within minutes.
5. **Validation lives at the ingress.** Every byte past GAP is assumed valid. Be strict here, be forgiving later.
6. **Listeners have two hops.** Bootstrap → topology → per-broker connect. Failures live at the second hop and look like the first.
7. **Non-wire-format messages will appear in your topic.** Assume it, defend against it, don't panic when you see `0x64`.
8. **`minikube image build` for iteration, `docker buildx` for "for real".** Multi-arch matters when your laptop is arm64 and the CI node is amd64.

---

## Red flags — don't move to Day 3 until

- You can explain when you'd pick a partition key vs round-robin, and why `meetingId` is the right key for us.
- `acks=0` vs `acks=1` vs `acks=all` trade-offs are instinctive — you don't have to look them up.
- You can explain how the idempotent producer survives a retry without duplicating (producer_id + sequence number) *and* when it doesn't (new producer session, new PID).
- You know why caching schema_id matters at 10k eps (one round-trip per record × 10k/s = SR melts).
- You can decode a Kafka message from bytes by hand — magic byte, 4-byte ID, Avro payload.
- You have a mental model for `BACKWARD` vs `FORWARD` vs `FULL` compatibility and which one matches your deploy ordering.
- You know why running GAP locally against cluster Kafka fails and can trace it to the listener configuration.

If any of these feel fuzzy: re-read the relevant "Core concepts" section, then re-run the "Points to ponder" question it maps to.

---

## What's next — Day 3 teaser

Day 3 adds the **catalog layer** — PostgreSQL + Hive Metastore. You'll learn why data pipelines split metadata from storage, what Thrift is, and why HMS doesn't watch S3. The events you're producing today become rows in `dl_analytics_bronze` in Phase 4 — Day 3 is what makes "where is that table?" a question with an answer.

---

## Reference — layout

```
day-2/
├── README.md                       this file
├── Makefile                        phase 0/1 targets (from day-1) + apps-* wrappers
├── minikube/                       cluster lifecycle scripts
├── k8s/                            Day 1 infra manifests (carried forward unchanged)
│   ├── 00-namespace/
│   ├── 01-minio/
│   └── 02-kafka/
├── scripts/                        Day 1 verification scripts
└── apps/                           Phase 2 — added today
    ├── README.md                   build + deploy flow
    ├── Makefile                    multi-arch buildx + deploy targets
    ├── mock-gap/                   FastAPI ingest gateway
    │   ├── app/                    main.py, producer.py, schemas.py
    │   ├── Dockerfile              multi-arch
    │   └── k8s/deployment.yaml
    ├── classroom-simulator/        event generator
    │   ├── sim/                    simulator.py, entities.py, events.py, config.py
    │   ├── Dockerfile              multi-arch
    │   └── k8s/deployment.yaml
    └── scripts/
        ├── smoke.sh                phase 2 smoke test
        ├── test_event_shapes.py    offline logic test (layer 1)
        └── decode_samples.py       in-cluster Kafka decoder (layer 2)
```
