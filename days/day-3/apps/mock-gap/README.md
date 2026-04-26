# Mock GAP — Global Analytics Platform Ingest Gateway

FastAPI service that mimics the ingest gateway of a Global Analytics Platform (GAP) — the HTTP edge that sits in front of Kafka, validates payloads, enforces schemas, and produces to the right topic.

## Endpoints
- `POST /api/gap/ingest` — accept JSON event, serialize Avro, produce to Kafka
- `GET  /healthz` — liveness
- `GET  /readyz` — readiness (producer started)
- `GET  /metrics` — Prometheus scrape

## Flow per request
1. Parse JSON body → validate `eventType` routes to a known topic
2. Lookup / register Avro schema from Schema Registry (by subject `<topic>-value`)
   - Cache schema_id per subject after first call (no per-record registry hit at steady state)
3. Serialize: `0x00` magic byte + 4-byte schema_id (big-endian) + Avro bytes
4. Produce to Kafka topic (`analytics` / `engagement-topic` / `webrtc-analytics`)
   - `acks=all` + idempotent producer → retries within a session don't duplicate
   - Key = `meetingId` → preserves per-meeting ordering within a partition

## Files
- [app/main.py](app/main.py) — FastAPI app + routes
- [app/producer.py](app/producer.py) — aiokafka + Confluent wire-format encoder + schema cache
- [app/schemas.py](app/schemas.py) — Avro schemas + eventType→topic routing
- [Dockerfile](Dockerfile) — multi-arch (`linux/amd64,linux/arm64`)
- [k8s/deployment.yaml](k8s/deployment.yaml) — Deployment + ClusterIP Service

## Environment
- `KAFKA_BOOTSTRAP_SERVERS` — default `data-pipeline-kafka-bootstrap:9092`
- `SCHEMA_REGISTRY_URL`     — default `http://schema-registry:8081`
- `LOG_LEVEL`               — default `INFO`

## Build + deploy
From the `apps/` directory:
```bash
make build-gap    # multi-arch buildx → load into minikube
make deploy-gap   # apply k8s manifest + wait for rollout
```

## Metrics exposed
- `gap_records_sent_total{topic,event_type}`
- `gap_records_failed_total{topic,reason}`
- `gap_produce_latency_seconds{topic}`
- `gap_schema_registry_hits_total{result}`

## Why a GAP-style gateway exists
In any real platform, you don't let clients produce to Kafka directly. A GAP-style ingest layer sits in front so you can centralize auth, enforce payload schemas, apply rate limits, and normalize event shapes before anything hits the log. This mock replicates that boundary — schema registration, wire-format serialization, topic routing. Nothing else.
