# Classroom Simulator — Event Generator

Headless async Python app that simulates a virtual-classroom workload and drives the pipeline end-to-end by POSTing events to Mock GAP.

## Model
- 5 zones (zoneId 1–5), weighted distribution (zone 1 busiest, zone 5 quietest)
- Each meeting: 1 teacher + N students, fixed simulated duration, ends with `meeting_end`
- Per meeting emits:
  - analytics: `meeting_start`, `user_join`, `user_leave`, `meeting_end`
  - webrtc-analytics: `mic_toggle`, `cam_toggle`, `network_quality`
  - engagement-topic: `speech_time`, `attention_score`
- `meeting_end` is the coordination signal — in Phase 4+ it triggers `JobControlStream` → Airflow

## Traffic shape
- Poisson-distributed inter-arrival times (`random.expovariate`) — realistically bursty
- Per-meeting rate = `EVENTS_PER_SECOND / CONCURRENT_MEETINGS`

## Environment
| Var                        | Default                       | What it does                                  |
|----------------------------|-------------------------------|-----------------------------------------------|
| `GAP_URL`                  | `http://mock-gap:8000`        | ingest endpoint                               |
| `EVENTS_PER_SECOND`        | `10`                          | total across all meetings                     |
| `CONCURRENT_MEETINGS`      | `3`                           | parallel meeting tasks                        |
| `MEETING_DURATION_SECONDS` | `120`                         | simulated length of one meeting               |
| `STUDENTS_PER_MEETING`     | `5`                           |                                               |
| `DURATION_SECONDS`         | `0`                           | total runtime; 0 = forever                    |
| `ZONE_WEIGHTS`             | `0.5,0.25,0.15,0.07,0.03`     | 5 values summing to 1                         |

## Files
- [sim/simulator.py](sim/simulator.py) — async main loop, Poisson arrivals, stats
- [sim/entities.py](sim/entities.py) — Teacher, Student, Meeting, zone picker
- [sim/events.py](sim/events.py) — event-shape generators per Avro schema
- [sim/config.py](sim/config.py) — env-driven config
- [Dockerfile](Dockerfile) — multi-arch
- [k8s/deployment.yaml](k8s/deployment.yaml) — Deployment (no Service; it's an outbound-only client)

## Build + deploy
From the `apps/` directory:
```bash
make build-sim
make deploy-sim
```

## Why headless
No UI — the goal is pipeline intuition, not a demo app. Every event it emits is visible in Kafka, Delta, and StarRocks. Adding a UI would distract from the learning objective.

## Production mapping
Stands in for real collaboration-platform clients calling `POST /api/gap/ingest`. Event shapes match the production Avro schemas exactly.
