# Phase 0 — Namespace + ResourceQuota + LimitRange

Creates the `data-pipeline` namespace, a `ResourceQuota` that caps what the full stack can consume, and a `LimitRange` that supplies default requests/limits for any pod that doesn't declare its own.

## Deploy

```bash
make phase0
```

## Quota

| Resource               | Requests | Limits  |
|------------------------|----------|---------|
| CPU                    | 20       | 32      |
| Memory                 | 48 Gi    | 96 Gi   |
| PersistentVolumeClaims | 40       | —       |

**Why these numbers:** StarRocks alone takes ~8 GB. Strimzi brokers + Spark driver/executors + Airflow worker + HMS + Postgres + MinIO stack up fast. 48 Gi requests / 96 Gi limits leaves headroom for `kube-system` and prevents one runaway pod from starving the rest.

## LimitRange (the partner of ResourceQuota)

| Field           | CPU    | Memory  |
|-----------------|--------|---------|
| `default` (limits) | 500m | 512 Mi |
| `defaultRequest`   | 100m | 128 Mi |
| `max` per container| 8    | 16 Gi  |

**Why this is needed:** as soon as a `ResourceQuota` declares `requests.cpu`/`limits.cpu`, the scheduler refuses to admit any pod whose containers don't declare those fields. That includes ad-hoc `kubectl run` pods, Jobs without explicit resources, and most upstream Helm charts. The `LimitRange` supplies sensible defaults so admission still succeeds. Authored manifests in this lab continue to set their own resources — the LimitRange is just a backstop.

You'll see this pattern in every prod K8s namespace: `ResourceQuota` (the cap) + `LimitRange` (the per-pod defaults + ceiling). They are designed to work together.

## Validate

```bash
make verify-phase0
```

Or manually:
```bash
kubectl get ns data-pipeline
kubectl -n data-pipeline describe resourcequota data-pipeline-quota
```

## The mental-model exercise

Try to schedule a pod that breaches the quota. It must be *rejected at admission* — not run and OOM later.

```bash
kubectl -n data-pipeline run too-big \
  --image=busybox --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"too-big","image":"busybox","resources":{"requests":{"cpu":"30","memory":"80Gi"},"limits":{"cpu":"30","memory":"80Gi"}}}]}}' \
  -- sleep 60
# expect: error from server (Forbidden): pods "too-big" is forbidden: exceeded quota
```

That rejection — at the API server, before the scheduler even sees it — is what a ResourceQuota *is*.

## Production mapping

In prod this would be a dedicated K8s namespace on the analytics EKS cluster with a similar quota policed by cluster admin controllers. The quota here serves two purposes: realistic boundary, and a safety net so the lab can't accidentally exhaust the minikube VM.
