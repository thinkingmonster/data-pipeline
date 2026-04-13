#!/usr/bin/env bash
# Minikube profile for lab 13 — data-pipeline.
# Sized for full stack: Strimzi Kafka, Spark Operator, Airflow, StarRocks, Hive Metastore, MinIO, Grafana.

set -euo pipefail

PROFILE="${PROFILE:-data-pipeline}"
CPUS="${CPUS:-10}"
MEMORY_MB="${MEMORY_MB:-24576}"   # 24 GB
DISK="${DISK:-80g}"
DRIVER="${DRIVER:-docker}"
K8S_VERSION="${K8S_VERSION:-v1.33.0}"

echo "Starting minikube profile '${PROFILE}' — ${CPUS} CPU / ${MEMORY_MB} MB / ${DISK} disk"

minikube start \
  --profile "${PROFILE}" \
  --driver "${DRIVER}" \
  --kubernetes-version "${K8S_VERSION}" \
  --cpus "${CPUS}" \
  --memory "${MEMORY_MB}" \
  --disk-size "${DISK}" \
  --container-runtime=containerd \
  --addons=metrics-server,dashboard,ingress

minikube profile "${PROFILE}"

kubectl config use-context "${PROFILE}"

echo
echo "Minikube ready."
echo "Profile:    ${PROFILE}"
echo "Context:    $(kubectl config current-context)"
echo "Nodes:"
kubectl get nodes -o wide
