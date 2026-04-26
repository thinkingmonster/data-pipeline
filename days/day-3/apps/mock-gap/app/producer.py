"""Async Kafka producer with Confluent Schema Registry wire-format serialization.

Wire format: [ 0x00 magic ][ 4-byte schema_id big-endian ][ Avro-encoded payload ]

Idempotent producer with acks=all: retries within one producer session are
de-duplicated by (producer_id, sequence_number). Paired with a per-eventType
idempotency key (derived from payload) from the simulator, two identical
client retries land on the same offset.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import struct
from typing import Any

import httpx
from aiokafka import AIOKafkaProducer
from fastavro import parse_schema, schemaless_writer
from prometheus_client import Counter, Histogram

from .schemas import route, subject_for

log = logging.getLogger(__name__)

MAGIC_BYTE = b"\x00"

records_sent = Counter(
    "gap_records_sent_total",
    "Total records successfully produced to Kafka",
    labelnames=("topic", "event_type"),
)
records_failed = Counter(
    "gap_records_failed_total",
    "Total records that failed to produce",
    labelnames=("topic", "reason"),
)
produce_latency = Histogram(
    "gap_produce_latency_seconds",
    "Time from ingest receipt to producer.send completion",
    labelnames=("topic",),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
schema_registry_hits = Counter(
    "gap_schema_registry_hits_total",
    "Schema Registry lookups/registrations",
    labelnames=("result",),  # cache_hit | registered | lookup_fail
)


class SchemaCache:
    """Registers schemas once per subject, caches the resulting schema_id.

    Uses POST /subjects/<subject>/versions (register-if-absent). The Registry
    returns the existing ID if the schema is already registered.
    """

    def __init__(self, registry_url: str, http: httpx.AsyncClient) -> None:
        self._url = registry_url.rstrip("/")
        self._http = http
        self._cache: dict[str, tuple[int, dict]] = {}
        self._lock = asyncio.Lock()

    async def get(self, subject: str, schema: dict) -> tuple[int, dict]:
        cached = self._cache.get(subject)
        if cached is not None:
            schema_registry_hits.labels(result="cache_hit").inc()
            log.debug("sr cache_hit subject=%s schema_id=%d", subject, cached[0])
            return cached

        async with self._lock:
            cached = self._cache.get(subject)
            if cached is not None:
                schema_registry_hits.labels(result="cache_hit").inc()
                log.debug("sr cache_hit (post-lock) subject=%s schema_id=%d", subject, cached[0])
                return cached

            log.debug("sr cache_miss subject=%s -> POST %s/subjects/%s/versions",
                      subject, self._url, subject)
            payload = {"schema": json.dumps(schema), "schemaType": "AVRO"}
            resp = await self._http.post(
                f"{self._url}/subjects/{subject}/versions",
                json=payload,
                headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
                timeout=10.0,
            )
            if resp.status_code >= 300:
                schema_registry_hits.labels(result="lookup_fail").inc()
                raise RuntimeError(
                    f"schema registry returned {resp.status_code} for {subject}: {resp.text}"
                )
            schema_id = int(resp.json()["id"])
            parsed = parse_schema(schema)
            self._cache[subject] = (schema_id, parsed)
            schema_registry_hits.labels(result="registered").inc()
            log.info("registered subject=%s schema_id=%d (sr responded %d)",
                     subject, schema_id, resp.status_code)
            return schema_id, parsed


def encode(schema_id: int, parsed_schema: dict, record: dict[str, Any]) -> bytes:
    """Serialize in Confluent wire format."""
    buf = io.BytesIO()
    buf.write(MAGIC_BYTE)
    buf.write(struct.pack(">I", schema_id))
    schemaless_writer(buf, parsed_schema, record)
    return buf.getvalue()


class GapProducer:
    def __init__(
        self,
        bootstrap_servers: str,
        registry_url: str,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._registry_url = registry_url
        self._producer: AIOKafkaProducer | None = None
        self._http: httpx.AsyncClient | None = None
        self._cache: SchemaCache | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient()
        self._cache = SchemaCache(self._registry_url, self._http)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            acks="all",
            enable_idempotence=True,
            linger_ms=10,
            max_batch_size=32_768,
            request_timeout_ms=30_000,
            compression_type="lz4",
        )
        await self._producer.start()
        log.info("producer started bootstrap=%s registry=%s", self._bootstrap, self._registry_url)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
        if self._http is not None:
            await self._http.aclose()

    async def ingest(self, event: dict[str, Any]) -> dict[str, Any]:
        """Validate → serialize → produce. Returns routing metadata."""
        assert self._producer is not None and self._cache is not None

        event_type = event.get("eventType")
        if not isinstance(event_type, str):
            raise ValueError("missing or non-string eventType")

        topic, schema = route(event_type)
        subject = subject_for(topic)
        log.debug("route eventType=%s -> topic=%s subject=%s", event_type, topic, subject)

        schema_id, parsed = await self._cache.get(subject, schema)

        try:
            wire_bytes = encode(schema_id, parsed, event)
        except Exception as exc:
            records_failed.labels(topic=topic, reason="encode").inc()
            raise ValueError(f"payload does not match schema {subject}: {exc}") from exc

        if log.isEnabledFor(logging.DEBUG):
            hex_preview = wire_bytes[:16].hex(" ")
            log.debug("encoded wire=[magic|sid=%d|avro] %dB first16=%s",
                     schema_id, len(wire_bytes), hex_preview)

        # Partition by meetingId — preserves per-meeting ordering.
        key = str(event.get("meetingId", "")).encode("utf-8") or None

        with produce_latency.labels(topic=topic).time():
            try:
                meta = await self._producer.send_and_wait(topic, wire_bytes, key=key)
            except Exception as exc:
                records_failed.labels(topic=topic, reason="produce").inc()
                raise RuntimeError(f"kafka send failed: {exc}") from exc

        records_sent.labels(topic=topic, event_type=event_type).inc()
        log.debug("kafka sent topic=%s partition=%d offset=%d key=%s schema_id=%d",
                 meta.topic, meta.partition, meta.offset,
                 key.decode() if key else None, schema_id)
        return {
            "topic": meta.topic,
            "partition": meta.partition,
            "offset": meta.offset,
            "schema_id": schema_id,
        }
