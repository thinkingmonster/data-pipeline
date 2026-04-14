# Day 1 — Foundation: Kafka, Schema Registry, and Object Storage

Self-contained snapshot for Day 1. Subsequent days copy this folder and add to it.

Covers **Phase 0** (Minikube + Namespace + ResourceQuota) and **Phase 1** (MinIO + Strimzi Kafka + Schema Registry) from [../../LEARNING-ROADMAP.md](../../LEARNING-ROADMAP.md).

---

## What you'll have at the end of today

- A dedicated `data-pipeline` minikube profile sized for the full stack (10 CPU / 24 GB)
- A `data-pipeline` namespace with a ResourceQuota that admission-controls every later phase
- MinIO (S3-compatible) with bucket `analytics-lab` ready
- A 3-broker Strimzi Kafka cluster in KRaft mode
- 4 Kafka topics declared as `KafkaTopic` CRs
- Confluent Schema Registry serving `:8081`, backed by the `_schemas` compacted topic

---

## Run order

```bash
# 0. Bring up the cluster (first time only)
make minikube-up

# 1. Phase 0 — namespace + quota
make phase0
make verify-phase0

# 2. Phase 1a — MinIO
make minio
make verify-minio

# 3. Phase 1b — Strimzi operator + Kafka cluster + topics
make kafka
make kafka-topics
make verify-kafka

# 4. Phase 1c — Schema Registry
make schema-registry
make verify-schema-registry

# Status overview
make status
```

Tear-down at the end of the day (state preserved):
```bash
make minikube-stop
```

Wipe everything (start fresh tomorrow):
```bash
make minikube-destroy
```

---

## Mental models to lock in today

1. **Namespace = soft boundary, ResourceQuota = hard boundary.** Try scheduling a pod that breaches the quota and watch admission reject it.
2. **Kafka is a replicated log, not a queue.** Messages survive consumption; consumer groups track their own offsets.
3. **Schema ID ≠ schema version.** Re-read [../../docs/confluent-schema-registry.md](../../docs/confluent-schema-registry.md) until they feel distinct.
4. **MinIO/S3 is not a filesystem.** No atomic rename, no directories — only key prefixes. Delta Lake works only because of `put-if-absent` on `_delta_log/N.json`.
5. **Data plane vs control plane.** Brokers move bytes (data plane). Strimzi operator + KRaft controllers + Schema Registry are control plane. A control-plane outage stops new deploys but in-flight traffic survives.

---

## Hands-on checkpoints (from LEARNING-ROADMAP)

- [ ] `minikube profile list` shows `data-pipeline` as active
- [ ] `kubectl describe resourcequota -n data-pipeline data-pipeline-quota` — read Used vs Hard
- [ ] Schedule a pod requesting more CPU than the quota and watch it be rejected
- [ ] Produce a test message with `kafka-console-producer`, consume with `kafka-console-consumer`
- [ ] `kubectl get kafkatopic -n data-pipeline` — see CRs, not raw topics
- [ ] Register an Avro schema via `curl -X POST .../subjects/analytics-value/versions`
- [ ] `curl /schemas/ids/1` — round-trip the schema by ID
- [ ] Write a file to MinIO via `mc cp`, read it back via `mc cat`
- [ ] Kill a Strimzi broker pod — watch partitions fail over

---

## Layout

```
day-1/
├── README.md                       this file
├── Makefile                        all phase + verify targets
├── minikube/                       cluster lifecycle scripts
│   ├── profile.sh
│   ├── stop.sh
│   └── destroy.sh
├── k8s/
│   ├── 00-namespace/               Phase 0 manifests
│   ├── 01-minio/                   Phase 1a manifests
│   └── 02-kafka/                   Phase 1b + 1c manifests
└── scripts/                        verification scripts
    ├── verify-phase0.sh
    ├── verify-minio.sh
    ├── verify-kafka.sh
    └── verify-schema-registry.sh
```

