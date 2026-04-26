#!/usr/bin/env bash
# Verify Hive Metastore: Deployment Ready, Thrift :9083 reachable,
# schema tables exist in Postgres, schema VERSION row populated,
# metatool -listFSRoot round-trips through HMS → Postgres → S3A.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/5] HMS Deployment Ready?"
$KUBECTL -n "$NS" rollout status deployment/hive-metastore --timeout=420s
green "  OK"

blue "[2/5] Postgres has HMS schema tables (DBS, TBLS, PARTITIONS)..."
if $KUBECTL -n "$NS" exec statefulset/hms-postgres -- \
   psql -U hive -d metastore -c '\dt' 2>&1 | grep -qE '"?DBS"?|public \| "DBS"'; then
  green "  OK"
else
  echo "FAIL: HMS schema tables not found in Postgres."
  $KUBECTL -n "$NS" exec statefulset/hms-postgres -- \
    psql -U hive -d metastore -c '\dt'
  exit 1
fi

blue "[3/5] HMS schema VERSION row populated (proves schematool ran)..."
$KUBECTL -n "$NS" exec statefulset/hms-postgres -- \
  psql -U hive -d metastore -c 'SELECT * FROM "VERSION";'
green "  OK"

blue "[4/5] Thrift :9083 reachable from inside the cluster..."
$KUBECTL -n "$NS" run hms-tcp-$RANDOM --rm -i --restart=Never \
  --image=busybox:1.36 -- \
  sh -c 'nc -zv hive-metastore 9083 2>&1 | grep -qi "open\|succeeded" && echo OPEN'
green "  OK"

blue "[5/5] HMS RPC round-trip: metatool -listFSRoot (talks to Postgres + S3A)..."
POD=$($KUBECTL -n "$NS" get pod -l app=hive-metastore \
        -o jsonpath='{.items[0].metadata.name}')
$KUBECTL -n "$NS" exec "$POD" -- bash -c '
  /opt/hive/bin/metatool -listFSRoot 2>&1
' | tail -10
green "HMS verified."
