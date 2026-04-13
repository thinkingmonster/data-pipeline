# Phase 1b + 1c — Strimzi Kafka (KRaft) + Confluent Schema Registry

3-broker Kafka cluster managed by the Strimzi operator running in KRaft mode (no Zookeeper), plus Confluent Schema Registry backed by the `_schemas` compacted topic.

## Resources

| Kind            | Name                  | Purpose                                          |
|-----------------|-----------------------|--------------------------------------------------|
| Operator        | `strimzi-cluster-operator` | watches CRs, reconciles Kafka into pods     |
| KafkaNodePool   | `controller`          | 3 KRaft controllers (10 Gi each)                 |
| KafkaNodePool   | `broker`              | 3 brokers (20 Gi each)                           |
| Kafka           | `data-pipeline`       | the cluster CR (KRaft, plaintext :9092 internal) |
| KafkaTopic      | `analytics`           | 3 partitions, RF=3                               |
| KafkaTopic      | `engagement-topic`    | 3 partitions, RF=3                               |
| KafkaTopic      | `webrtc-analytics`    | 3 partitions, RF=3                               |
| KafkaTopic      | `_schemas`            | 1 partition, RF=3, **compacted** — Registry's WAL |
| Deployment      | `schema-registry`     | Confluent Schema Registry :8081                  |
| Service         | `schema-registry`     | ClusterIP :8081                                  |

## Deploy

```bash
make kafka              # operator + cluster + topics
make schema-registry    # registry deployment
```

## Verify

```bash
make verify-kafka
make verify-schema-registry
```

## Bootstrap address (memorize this)

```
PLAINTEXT://data-pipeline-kafka-bootstrap:9092
```

Strimzi names the bootstrap service `<cluster>-kafka-bootstrap`. Every client (Schema Registry, Spark, mock-GAP) uses this DNS name from inside the namespace.

## Why Strimzi (not Redpanda or Kafka-on-Helm)

- **Closer to prod MSK behavior** — real Kafka protocol quirks, ISR behavior, partition assignment edge cases.
- **CRD-first** — `KafkaTopic` CRs replace `kafka-topics.sh`. Declarative > imperative; survives operator restart.
- **NodePool API** (the new way as of Strimzi 0.36+) — separates broker/controller fleets cleanly under KRaft.

## Why KRaft (not Zookeeper)

- Zookeeper is gone in Kafka 4.0. Get used to KRaft now.
- Brokers ↔ controllers separation — controllers are the metadata quorum (the old ZK job), brokers serve client traffic.
- One less stateful service to operate.

## Why these topic settings

- **`replication.factor=3, min.insync.replicas=2`** — survives one broker loss without write availability loss. The sane default.
- **`_schemas` is compacted, single partition** — ordering of schema commits matters globally; compaction keeps only the latest value per subject-version key.
- **3 partitions per data topic** — matches the broker count for parallelism. In prod we'd run 100+ partitions per topic.

## Mental model — data plane vs control plane

- **Data plane:** brokers moving bytes. Down → outage.
- **Control plane:** Strimzi operator, KRaft controllers, Schema Registry. Down → no new deploys / new schemas, but in-flight traffic survives.

This distinction matters in incident triage: "Are clients failing to *produce*, or failing to *register a new schema*?" — different blast radius, different urgency.

## Production mapping

Amazon MSK, Kafka 3.8+, 3 brokers, SSL+SASL, 100 partitions per topic. We drop SSL and use 3 partitions — same wire protocol, same semantics.
