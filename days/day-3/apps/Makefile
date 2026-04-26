SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PROFILE   ?= data-pipeline
NS        ?= data-pipeline
TAG       ?= day2
PLATFORMS ?= linux/amd64,linux/arm64
BUILDER   ?= lab13-builder
KCTX      := --context=$(PROFILE)
KUBECTL   := kubectl $(KCTX)

GAP_IMAGE := mock-gap:$(TAG)
SIM_IMAGE := classroom-simulator:$(TAG)

.PHONY: help
help: ## show targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## --- buildx setup ---

.PHONY: buildx-init
buildx-init: ## create a shared buildx builder (run once)
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 || \
		docker buildx create --name $(BUILDER) --driver docker-container --use
	@docker buildx use $(BUILDER)
	@docker buildx inspect --bootstrap >/dev/null

## --- multi-arch builds (build + export OCI tarballs, then load into minikube) ---
## Rationale: `--load` doesn't support multi-arch in a single step. We build to
## an OCI tarball containing both platforms, then use `minikube image load` —
## which extracts the arch matching the minikube node.

.PHONY: build-gap
build-gap: buildx-init ## build multi-arch mock-gap image and load into minikube
	@mkdir -p .build
	docker buildx build \
		--platform $(PLATFORMS) \
		--tag $(GAP_IMAGE) \
		--output type=oci,dest=.build/mock-gap.tar \
		mock-gap
	minikube --profile $(PROFILE) image load .build/mock-gap.tar
	@rm -f .build/mock-gap.tar
	@minikube --profile $(PROFILE) image ls | grep mock-gap || true

.PHONY: build-sim
build-sim: buildx-init ## build multi-arch classroom-simulator image and load into minikube
	@mkdir -p .build
	docker buildx build \
		--platform $(PLATFORMS) \
		--tag $(SIM_IMAGE) \
		--output type=oci,dest=.build/classroom-simulator.tar \
		classroom-simulator
	minikube --profile $(PROFILE) image load .build/classroom-simulator.tar
	@rm -f .build/classroom-simulator.tar
	@minikube --profile $(PROFILE) image ls | grep classroom-simulator || true

.PHONY: build
build: build-gap build-sim ## build both images multi-arch + load into minikube

## --- single-arch fast path (iteration) ---
## Skips buildx; builds inside minikube's docker daemon for the node's native
## arch. Faster for tight edit/test loops. Use `make build` for the real thing.

.PHONY: build-fast-gap
build-fast-gap: ## single-arch build of mock-gap inside minikube (fast iter)
	minikube --profile $(PROFILE) image build -t $(GAP_IMAGE) mock-gap

.PHONY: build-fast-sim
build-fast-sim: ## single-arch build of classroom-simulator inside minikube (fast iter)
	minikube --profile $(PROFILE) image build -t $(SIM_IMAGE) classroom-simulator

.PHONY: build-fast
build-fast: build-fast-gap build-fast-sim ## both, fast path

## --- deploy ---

.PHONY: deploy-gap
deploy-gap: ## apply mock-gap Deployment + Service
	$(KUBECTL) -n $(NS) apply -f mock-gap/k8s/deployment.yaml
	$(KUBECTL) -n $(NS) rollout status deployment/mock-gap --timeout=120s

.PHONY: deploy-sim
deploy-sim: ## apply classroom-simulator Deployment
	$(KUBECTL) -n $(NS) apply -f classroom-simulator/k8s/deployment.yaml
	$(KUBECTL) -n $(NS) rollout status deployment/classroom-simulator --timeout=120s

.PHONY: deploy
deploy: deploy-gap deploy-sim ## deploy both

.PHONY: undeploy
undeploy: ## delete both Deployments + Service
	-$(KUBECTL) -n $(NS) delete -f classroom-simulator/k8s/deployment.yaml
	-$(KUBECTL) -n $(NS) delete -f mock-gap/k8s/deployment.yaml

## --- convenience ---

.PHONY: up
up: build deploy ## full phase 2: multi-arch build + deploy

.PHONY: up-fast
up-fast: build-fast deploy ## single-arch fast build + deploy

.PHONY: restart
restart: ## force a fresh pod (useful after build-fast when tag is unchanged)
	$(KUBECTL) -n $(NS) rollout restart deployment/mock-gap
	$(KUBECTL) -n $(NS) rollout restart deployment/classroom-simulator

## --- smoke ---

.PHONY: smoke
smoke: ## send a synthetic event via curl through mock-gap
	bash scripts/smoke.sh

.PHONY: port-forward-gap
port-forward-gap: ## port-forward mock-gap to localhost:8000
	$(KUBECTL) -n $(NS) port-forward svc/mock-gap 8000:8000

.PHONY: logs-gap
logs-gap: ## tail mock-gap logs
	$(KUBECTL) -n $(NS) logs -f deploy/mock-gap

.PHONY: logs-sim
logs-sim: ## tail simulator logs
	$(KUBECTL) -n $(NS) logs -f deploy/classroom-simulator

.PHONY: status
status: ## show phase 2 resources
	$(KUBECTL) -n $(NS) get deploy,svc,pod -l 'app in (mock-gap,classroom-simulator)'
