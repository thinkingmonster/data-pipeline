# Phase 2 Apps — Mock GAP + Classroom Simulator

Two services that together form the event-source layer:

- [mock-gap/](mock-gap/) — FastAPI ingest gateway (JSON → Avro → Kafka)
- [classroom-simulator/](classroom-simulator/) — async client that POSTs realistic classroom events to GAP

See each sub-README for architecture and env knobs.

## Multi-arch build + deploy

Both images are built multi-arch (`linux/amd64,linux/arm64`) via `docker buildx`, then loaded into the minikube profile.

```bash
# one-time: set up the shared buildx builder
make buildx-init

# build both images multi-arch + load into minikube
make build

# apply k8s manifests
make deploy

# or one-shot
make up
```

### Fast iteration

`docker buildx` is slower than a native docker build. For tight edit/test loops:

```bash
make build-fast    # single-arch via `minikube image build` (node native arch)
make restart       # force pod to pick up the new image (tag is reused)
```

Use `make build` (multi-arch) before committing or publishing.

## Smoke test

After `make deploy`, in one shell:

```bash
make port-forward-gap   # localhost:8000 → mock-gap:8000
```

In another:

```bash
make smoke              # POSTs one analytics event, tails kafka, lists SR subjects
```

## Targets

See `make help` for the full list. Highlights:

| Target            | What                                           |
|-------------------|------------------------------------------------|
| `buildx-init`     | create the shared buildx builder (one-time)    |
| `build`           | multi-arch build both images + load to minikube |
| `build-fast`      | single-arch via minikube — fast iteration       |
| `deploy`          | apply both Deployments                         |
| `up`              | `build` + `deploy`                             |
| `restart`         | rollout restart both deployments               |
| `smoke`           | send one event + check Kafka + check SR        |
| `logs-gap`/`logs-sim` | tail pod logs                              |
| `undeploy`        | delete both Deployments                        |
