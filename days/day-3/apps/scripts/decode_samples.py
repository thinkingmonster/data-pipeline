"""In-cluster Kafka decoder for Phase 2 verification.

Consumes a handful of messages from each topic, parses the Confluent wire
format, fetches the referenced schema from Schema Registry, and prints the
decoded payload.

Run inside the mock-gap pod (already has aiokafka + fastavro + httpx):

    POD=$(kubectl -n data-pipeline get pod -l app=mock-gap -o jsonpath='{.items[0].metadata.name}')
    kubectl cp apps/scripts/decode_samples.py $POD:/tmp/decode_samples.py -n data-pipeline
    kubectl exec -n data-pipeline $POD -- python /tmp/decode_samples.py

Non-wire-format messages (e.g. Day 1 `kafka-console-producer` leftovers with
magic byte != 0x00) are skipped with a count at the end.
"""
from __future__ import annotations

import asyncio
import io
import json
import struct

import httpx
from aiokafka import AIOKafkaConsumer
from fastavro import parse_schema, schemaless_reader

BOOTSTRAP = "data-pipeline-kafka-bootstrap:9092"
SR = "http://schema-registry:8081"
TOPICS = ("analytics", "engagement-topic", "webrtc-analytics")
SCAN_PER_TOPIC = 20
SHOW_PER_TOPIC = 3


async def scan(topic: str, http: httpx.AsyncClient) -> None:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        group_id=None,
    )
    await consumer.start()
    shown = 0
    non_wire = 0
    schema_cache: dict[int, dict] = {}
    try:
        for _ in range(SCAN_PER_TOPIC):
            if shown >= SHOW_PER_TOPIC:
                break
            try:
                msg = await asyncio.wait_for(consumer.getone(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            raw = msg.value
            if raw[0] != 0x00:
                non_wire += 1
                continue
            sid = struct.unpack(">I", raw[1:5])[0]
            if sid not in schema_cache:
                r = await http.get(f"{SR}/schemas/ids/{sid}")
                r.raise_for_status()
                schema_cache[sid] = parse_schema(json.loads(r.json()["schema"]))
            decoded = schemaless_reader(io.BytesIO(raw[5:]), schema_cache[sid])
            key = msg.key.decode() if msg.key else None
            print(
                f"  [p={msg.partition} off={msg.offset} key={key!r} "
                f"sid={sid} {len(raw)}B]"
            )
            print(f"    {json.dumps(decoded, default=str, sort_keys=True)}")
            shown += 1
    finally:
        await consumer.stop()
    if non_wire:
        print(f"  [{topic}] skipped {non_wire} non-wire-format messages "
              f"(e.g. Day 1 smoke-test leftovers)")


async def main() -> None:
    async with httpx.AsyncClient() as http:
        for topic in TOPICS:
            print(f"=== {topic} ===")
            try:
                await scan(topic, http)
            except Exception as exc:
                print(f"  ERROR: {exc}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
