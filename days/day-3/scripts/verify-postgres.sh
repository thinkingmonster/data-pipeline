#!/usr/bin/env bash
# Verify HMS Postgres: pod Ready, DB reachable, metastore database exists.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/3] Postgres StatefulSet Ready?"
$KUBECTL -n "$NS" rollout status statefulset/hms-postgres --timeout=120s
green "  OK"

blue "[2/3] psql \\l — list databases (expect 'metastore' present)..."
$KUBECTL -n "$NS" exec statefulset/hms-postgres -- \
  psql -U hive -d metastore -c '\l'
green "  OK"

blue "[3/3] Sanity: SELECT 1 through the Service (not the pod directly)..."
$KUBECTL -n "$NS" run pg-smoke-$RANDOM --rm -i --restart=Never \
  --image=postgres:16.4 --env=PGPASSWORD=hive -- \
  psql -h hms-postgres -U hive -d metastore -c 'SELECT 1 AS ok;'
green "  OK"

green "Postgres verified."
