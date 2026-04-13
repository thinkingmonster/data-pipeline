SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PROFILE ?= data-pipeline
NS      ?= data-pipeline
KCTX    := --context=$(PROFILE)
KUBECTL := kubectl $(KCTX)

.PHONY: help
help: ## show targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "  %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## --- minikube lifecycle ---

.PHONY: minikube-up
minikube-up: ## start the minikube profile sized for the data stack
	bash minikube/profile.sh

.PHONY: minikube-stop
minikube-stop: ## stop the minikube profile (keeps state)
	bash minikube/stop.sh

.PHONY: minikube-destroy
minikube-destroy: ## delete the minikube profile (wipes all state)
	bash minikube/destroy.sh

.PHONY: minikube-status
minikube-status: ## show minikube status + node info
	@minikube status --profile $(PROFILE) || true
	@$(KUBECTL) get nodes -o wide 2>/dev/null || true

## --- phase 0: foundation ---

.PHONY: phase0
phase0: ## deploy phase 0: namespace + resource quota
	$(KUBECTL) apply -f k8s/00-namespace/namespace.yaml
	$(KUBECTL) get ns $(NS)
	$(KUBECTL) -n $(NS) get resourcequota

.PHONY: verify-phase0
verify-phase0: ## verify phase 0 (namespace, quota, admission control)
	bash scripts/verify-phase0.sh

## --- phase 1a: MinIO ---

.PHONY: minio
minio: ## deploy MinIO (Secret, StatefulSet, Service, bucket-init Job)
	$(KUBECTL) -n $(NS) apply -f k8s/01-minio/secret.yaml
	$(KUBECTL) -n $(NS) apply -f k8s/01-minio/service.yaml
	$(KUBECTL) -n $(NS) apply -f k8s/01-minio/statefulset.yaml
	$(KUBECTL) -n $(NS) rollout status statefulset/minio --timeout=180s
	$(KUBECTL) -n $(NS) apply -f k8s/01-minio/bucket-init-job.yaml
	$(KUBECTL) -n $(NS) wait --for=condition=complete job/minio-bucket-init --timeout=120s

.PHONY: minio-console
minio-console: ## port-forward MinIO console to http://localhost:9001
	@echo "Console: http://localhost:9001 (user: minioadmin / pass: minioadmin)"
	$(KUBECTL) -n $(NS) port-forward svc/minio 9001:9001

.PHONY: verify-minio
verify-minio: ## verify MinIO is up and bucket exists
	bash scripts/verify-minio.sh

## --- phase 1b: Strimzi Kafka ---

.PHONY: kafka
kafka: kafka-operator kafka-cluster kafka-topics ## deploy Strimzi operator + Kafka cluster + topics

.PHONY: kafka-operator
kafka-operator: ## install Strimzi operator into the data-pipeline namespace
	bash k8s/02-kafka/install-strimzi.sh

.PHONY: kafka-cluster
kafka-cluster: ## deploy 3-broker KRaft Kafka cluster
	$(KUBECTL) -n $(NS) apply -f k8s/02-kafka/kafka-cluster.yaml
	@echo "Waiting for Kafka cluster to be Ready (this can take 3-5 minutes)..."
	$(KUBECTL) -n $(NS) wait kafka/data-pipeline --for=condition=Ready --timeout=600s

.PHONY: kafka-topics
kafka-topics: ## declare the four KafkaTopic CRs
	$(KUBECTL) -n $(NS) apply -f k8s/02-kafka/kafka-topics.yaml
	$(KUBECTL) -n $(NS) get kafkatopic

.PHONY: verify-kafka
verify-kafka: ## verify Kafka cluster + topics with produce/consume round-trip
	bash scripts/verify-kafka.sh

## --- phase 1c: Schema Registry ---

.PHONY: schema-registry
schema-registry: ## deploy Confluent Schema Registry
	$(KUBECTL) -n $(NS) apply -f k8s/02-kafka/schema-registry.yaml
	$(KUBECTL) -n $(NS) rollout status deployment/schema-registry --timeout=180s

.PHONY: verify-schema-registry
verify-schema-registry: ## verify Schema Registry by registering and re-fetching an Avro schema
	bash scripts/verify-schema-registry.sh

## --- convenience ---

.PHONY: up
up: minikube-up phase0 minio kafka schema-registry ## full Day 1: cluster + phase 0 + phase 1

.PHONY: status
status: ## show all resources in the data-pipeline namespace
	$(KUBECTL) -n $(NS) get all,pvc,cm,secret,kafka,kafkatopic
