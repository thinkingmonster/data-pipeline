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

## --- phase 2: apps (mock-gap + classroom-simulator) ---
## Apps live under ./apps/ (carried forward from Day 2).
## These wrappers delegate to the apps Makefile.

APPS_DIR := apps

.PHONY: apps-build
apps-build: ## multi-arch build both images + load into minikube
	$(MAKE) -C $(APPS_DIR) build

.PHONY: apps-build-fast
apps-build-fast: ## single-arch fast build via `minikube image build`
	$(MAKE) -C $(APPS_DIR) build-fast

.PHONY: apps-deploy
apps-deploy: ## deploy mock-gap + classroom-simulator
	$(MAKE) -C $(APPS_DIR) deploy

.PHONY: apps-up
apps-up: apps-build apps-deploy ## full phase 2: multi-arch build + deploy

.PHONY: apps-smoke
apps-smoke: ## run the phase-2 smoke test
	$(MAKE) -C $(APPS_DIR) smoke

.PHONY: apps-undeploy
apps-undeploy: ## tear down phase 2
	$(MAKE) -C $(APPS_DIR) undeploy

## --- phase 3a: PostgreSQL (HMS backend) ---

HMS_DIR := k8s/03-postgres-hms

.PHONY: postgres
postgres: ## deploy Postgres StatefulSet for HMS
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/postgres-secret.yaml
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/postgres-service.yaml
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/postgres-statefulset.yaml
	$(KUBECTL) -n $(NS) rollout status statefulset/hms-postgres --timeout=180s

.PHONY: verify-postgres
verify-postgres: ## verify Postgres is up and the 'metastore' DB exists
	bash scripts/verify-postgres.sh

## --- phase 3b: Hive Metastore (Thrift :9083) ---

.PHONY: hms-config
hms-config: ## apply hive-site.xml ConfigMap + S3A credential Secret
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/s3a-credentials-secret.yaml
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/hms-configmap.yaml

.PHONY: hms-deploy
hms-deploy: ## deploy Hive Metastore Deployment + Service
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/hms-deployment.yaml
	$(KUBECTL) -n $(NS) apply -f $(HMS_DIR)/hms-service.yaml
	$(KUBECTL) -n $(NS) rollout status deployment/hive-metastore --timeout=420s

.PHONY: hms
hms: hms-config hms-deploy ## full phase 3b: config + deploy HMS (entrypoint does schema init)

.PHONY: verify-hms
verify-hms: ## verify HMS end-to-end (Job + Deployment + Thrift + metatool)
	bash scripts/verify-hms.sh

.PHONY: hms-logs
hms-logs: ## tail Hive Metastore pod logs
	$(KUBECTL) -n $(NS) logs -f deployment/hive-metastore

.PHONY: hms-psql
hms-psql: ## open a psql shell against the HMS Postgres DB
	$(KUBECTL) -n $(NS) exec -it statefulset/hms-postgres -- \
	  psql -U hive -d metastore

## --- convenience ---

.PHONY: up
up: minikube-up phase0 minio kafka schema-registry apps-up postgres hms ## full Day 3: Day 2 stack + phase 3

.PHONY: up-phase3
up-phase3: postgres hms ## just phase 3 (assumes Day 2 is already up)

.PHONY: status
status: ## show all resources in the data-pipeline namespace
	$(KUBECTL) -n $(NS) get all,pvc,cm,secret,kafka,kafkatopic,deploy -l '!strimzi.io/name'
