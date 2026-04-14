#!/usr/bin/env bash
# Install the Strimzi operator into the data-pipeline namespace, watching only that namespace.
set -euo pipefail

NS="${NS:-data-pipeline}"
STRIMZI_VERSION="${STRIMZI_VERSION:-0.51.0}"
URL="https://github.com/strimzi/strimzi-kafka-operator/releases/download/${STRIMZI_VERSION}/strimzi-cluster-operator-${STRIMZI_VERSION}.yaml"

echo "Installing Strimzi operator ${STRIMZI_VERSION} into namespace ${NS}..."

# The upstream manifest hard-codes 'myproject' as the watch namespace.
# We rewrite it to ${NS} on the fly with sed and apply.
curl -fsSL "${URL}" \
  | sed "s|namespace: myproject|namespace: ${NS}|g" \
  | kubectl -n "${NS}" apply -f -

echo "Waiting for the operator deployment to be Available..."
kubectl -n "${NS}" rollout status deployment/strimzi-cluster-operator --timeout=180s

echo "Strimzi operator installed."
kubectl -n "${NS}" get pods -l name=strimzi-cluster-operator
