"""Offline sanity test — no Kafka, no HTTP, no Schema Registry.

Verifies:
  1. Every simulator event routes to a known eventType in mock-gap's router.
  2. Every simulator event validates against its target Avro schema (fastavro).
  3. Wire-format encode → decode round-trips cleanly.
  4. Zone-weight distribution roughly matches config.

Run from days/day-2/apps/:
  .venv/bin/python scripts/test_event_shapes.py
"""
from __future__ import annotations

import io
import struct
import sys
from collections import Counter
from pathlib import Path

# Make both app packages importable
HERE = Path(__file__).resolve().parent
APPS = HERE.parent
sys.path.insert(0, str(APPS / "mock-gap"))
sys.path.insert(0, str(APPS / "classroom-simulator"))

from fastavro import parse_schema, schemaless_reader, validate  # noqa: E402

from app.schemas import EVENT_ROUTING, route, subject_for  # noqa: E402
from app.producer import MAGIC_BYTE, encode  # noqa: E402
from sim.entities import Meeting, pick_zone  # noqa: E402
from sim.events import (  # noqa: E402
    meeting_end,
    meeting_start,
    random_mid_stream,
)


def header(label: str) -> None:
    print(f"\n=== {label} ===")


def check_routing_coverage() -> bool:
    """Every event type used by the simulator must be in the GAP router."""
    header("1. routing coverage")
    # Generate one of each kind and confirm router knows them
    now_ms = 1_700_000_000_000
    m = Meeting.new(zone_id=1, now_ms=now_ms, duration_ms=60_000, student_count=3)

    samples = [
        meeting_start(m),
        meeting_end(m),
    ]
    for _ in range(50):
        samples.append(random_mid_stream(m))

    missing = []
    for ev in samples:
        et = ev.get("eventType")
        if et not in EVENT_ROUTING:
            missing.append(et)
    if missing:
        print(f"  FAIL — unrouted eventTypes: {set(missing)}")
        return False
    print(f"  OK — all {len(samples)} events route cleanly (types: "
          f"{sorted({e['eventType'] for e in samples})})")
    return True


def check_schema_validation() -> bool:
    """Each event must validate against its declared Avro schema."""
    header("2. Avro schema validation")
    now_ms = 1_700_000_000_000
    m = Meeting.new(zone_id=1, now_ms=now_ms, duration_ms=60_000, student_count=3)

    samples = [meeting_start(m), meeting_end(m)]
    samples += [random_mid_stream(m) for _ in range(200)]

    by_subject: Counter[str] = Counter()
    failures = []
    for ev in samples:
        topic, schema = route(ev["eventType"])
        parsed = parse_schema(schema)
        try:
            validate(ev, parsed, strict=True)
            by_subject[subject_for(topic)] += 1
        except Exception as exc:
            failures.append((ev.get("eventType"), str(exc), ev))

    if failures:
        print(f"  FAIL — {len(failures)} events failed validation")
        for et, msg, ev in failures[:3]:
            print(f"    - {et}: {msg}\n      event={ev}")
        return False

    for subj, n in by_subject.items():
        print(f"  OK — {subj}: {n} events validated")
    return True


def check_wire_format_roundtrip() -> bool:
    """encode() → parse header + Avro decode → original dict."""
    header("3. wire-format encode/decode round-trip")
    now_ms = 1_700_000_000_000
    m = Meeting.new(zone_id=3, now_ms=now_ms, duration_ms=60_000, student_count=3)

    ev = meeting_start(m)
    topic, schema = route(ev["eventType"])
    parsed = parse_schema(schema)
    fake_schema_id = 42

    wire = encode(fake_schema_id, parsed, ev)

    # Confluent wire format: [0x00][4B big-endian id][Avro]
    if wire[0:1] != MAGIC_BYTE:
        print(f"  FAIL — bad magic byte: {wire[0:1]!r}")
        return False
    sid = struct.unpack(">I", wire[1:5])[0]
    if sid != fake_schema_id:
        print(f"  FAIL — schema_id mismatch: {sid} != {fake_schema_id}")
        return False

    decoded = schemaless_reader(io.BytesIO(wire[5:]), parsed)
    if decoded != ev:
        print("  FAIL — decoded payload does not match original")
        print(f"    original: {ev}")
        print(f"    decoded:  {decoded}")
        return False

    print(f"  OK — magic=0x00, schema_id={sid}, {len(wire)}B total, payload matches")
    return True


def check_zone_distribution(trials: int = 10_000) -> bool:
    """Weighted zone picker should approximate the configured weights."""
    header(f"4. zone distribution (n={trials})")
    weights = (0.5, 0.25, 0.15, 0.07, 0.03)
    counts = Counter(pick_zone(weights) for _ in range(trials))
    print("  zone  observed   expected")
    bad = False
    for z, w in zip(range(1, 6), weights):
        obs = counts[z] / trials
        delta = abs(obs - w)
        marker = "OK" if delta < 0.02 else "WARN"
        if marker == "WARN":
            bad = True
        print(f"    {z}    {obs:>6.1%}    {w:>6.1%}   [{marker}]")
    return not bad


def main() -> int:
    results = [
        check_routing_coverage(),
        check_schema_validation(),
        check_wire_format_roundtrip(),
        check_zone_distribution(),
    ]
    print()
    if all(results):
        print("ALL CHECKS PASSED")
        return 0
    print("SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
