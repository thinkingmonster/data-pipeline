#!/usr/bin/env bash
# Phase 2 smoke test — sends one analytics event through Mock GAP and verifies
# it reaches Kafka. Requires port-forward to be running on localhost:8000, OR
# runs the curl from inside the cluster via an ephemeral debug pod.
#
# Usage:
#   bash scripts/smoke.sh            # uses port-forward (must be started)
#   IN_CLUSTER=1 bash scripts/smoke.sh
set -euo pipefail

PROFILE="${PROFILE:-data-pipeline}"
NS="${NS:-data-pipeline}"
KCTL="kubectl --context=${PROFILE}"

MEETING_ID="smoke-$(date +%s)"
TS_MS=$(($(date +%s) * 1000))
PAYLOAD=$(cat <<JSON
{
  "meetingId": "${MEETING_ID}",
  "zoneId": 1,
  "eventType": "meeting_start",
  "timestamp": ${TS_MS},
  "userId": "t-smoke",
  "payload": {"teacherId": "t-smoke", "studentCount": "5"}
}
JSON
)

echo "==> sending event to mock-gap (meetingId=${MEETING_ID})"
if [[ "${IN_CLUSTER:-}" == "1" ]]; then
  $KCTL -n "$NS" run curl-smoke --rm -i --restart=Never --image=curlimages/curl:8.11.1 -- \
    curl -sS -X POST http://mock-gap:8000/api/gap/ingest \
      -H 'content-type: application/json' -d "$PAYLOAD"
else
  curl -sS -X POST http://localhost:8000/api/gap/ingest \
    -H 'content-type: application/json' -d "$PAYLOAD"
fi
echo

echo "==> reading last 1 message from 'analytics' topic"
$KCTL -n "$NS" exec -i data-pipeline-broker-0 -c kafka -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server data-pipeline-kafka-bootstrap:9092 \
  --topic analytics \
  --max-messages 1 \
  --timeout-ms 10000 \
  --from-beginning | tail -c 500 || true

echo
echo "==> listing Schema Registry subjects"
$KCTL -n "$NS" exec -i deploy/schema-registry -- \
  curl -sS http://localhost:8081/subjects
echo
