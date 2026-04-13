#!/usr/bin/env bash
# Verify Schema Registry: register an Avro schema, fetch it back by ID, list subjects.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"
SR_URL="http://schema-registry:8081"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/4] Schema Registry deployment Ready?"
$KUBECTL -n "$NS" rollout status deployment/schema-registry --timeout=60s
green "  OK"

POD="sr-check-$RANDOM"

blue "[2/4] Register an Avro schema for subject 'analytics-value'..."
SCHEMA='{"schema":"{\"type\":\"record\",\"name\":\"AnalyticsEvent\",\"fields\":[{\"name\":\"meetingId\",\"type\":\"string\"},{\"name\":\"zoneId\",\"type\":\"int\"},{\"name\":\"ts\",\"type\":\"long\"}]}"}'
$KUBECTL -n "$NS" run "$POD" --rm -it --restart=Never --image=curlimages/curl:8.10.1 --command -- \
  sh -c "
    set -e
    echo 'POST schema...'
    curl -sS -X POST -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
      --data '${SCHEMA}' \
      ${SR_URL}/subjects/analytics-value/versions
    echo
    echo 'GET schemas/ids/1...'
    curl -sS ${SR_URL}/schemas/ids/1
    echo
    echo 'GET subjects...'
    curl -sS ${SR_URL}/subjects
    echo
    echo 'GET subjects/analytics-value/versions...'
    curl -sS ${SR_URL}/subjects/analytics-value/versions
    echo
  "

green "Schema Registry verified."
