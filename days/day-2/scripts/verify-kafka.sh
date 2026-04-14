#!/usr/bin/env bash
# Verify Kafka: cluster Ready, topics declared, produce+consume round-trip.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"
BOOTSTRAP="data-pipeline-kafka-bootstrap:9092"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/4] Kafka cluster condition Ready?"
$KUBECTL -n "$NS" wait kafka/data-pipeline --for=condition=Ready --timeout=60s
green "  OK"

blue "[2/4] KafkaTopic CRs reconciled to Ready?"
$KUBECTL -n "$NS" get kafkatopic
for t in analytics engagement-topic webrtc-analytics schemas-registry; do
  $KUBECTL -n "$NS" wait kafkatopic/"$t" --for=condition=Ready --timeout=60s
done
green "  OK"

blue "[3/4] Broker pods?"
$KUBECTL -n "$NS" get pods -l strimzi.io/cluster=data-pipeline,strimzi.io/broker-role=true

blue "[4/4] Produce + consume round-trip on 'analytics'?"
# --attach=true --stdin=false streams pod stdout back to this shell even when
# we have no TTY (e.g. running from a script or CI). Don't use -it here.
PROD_POD="kafka-roundtrip-$RANDOM"
MSG="day-1-smoke-$(date +%s)"
$KUBECTL -n "$NS" run "$PROD_POD" --rm --restart=Never --attach=true --stdin=false \
  --image=quay.io/strimzi/kafka:0.51.0-kafka-4.1.0 \
  --command -- /bin/sh -c "
    set -e
    echo '${MSG}' | bin/kafka-console-producer.sh \
      --bootstrap-server ${BOOTSTRAP} --topic analytics
    echo '--- consumed: ---'
    bin/kafka-console-consumer.sh \
      --bootstrap-server ${BOOTSTRAP} --topic analytics \
      --from-beginning --max-messages 1 --timeout-ms 15000
  "

green "Kafka verified."
