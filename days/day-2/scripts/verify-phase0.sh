#!/usr/bin/env bash
# Verify Phase 0: namespace exists, ResourceQuota is enforced.
set -euo pipefail

NS="${NS:-data-pipeline}"
PROFILE="${PROFILE:-data-pipeline}"
KUBECTL="kubectl --context=${PROFILE}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "[1/3] Namespace exists?"
$KUBECTL get ns "$NS" >/dev/null && green "  OK: namespace ${NS}"

blue "[2/3] ResourceQuota present and reporting Used vs Hard?"
$KUBECTL -n "$NS" describe resourcequota data-pipeline-quota
green "  OK: quota visible"

blue "[3/3] Admission control rejects an over-quota pod?"
set +e
out=$($KUBECTL -n "$NS" run too-big --image=busybox --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"too-big","image":"busybox","resources":{"requests":{"cpu":"30","memory":"80Gi"},"limits":{"cpu":"30","memory":"80Gi"}}}]}}' \
  -- sleep 60 2>&1)
rc=$?
set -e
if [[ $rc -ne 0 && "$out" == *"forbidden"* ]]; then
  green "  OK: admission rejected the over-quota pod (this is what we want)"
  echo "  message: ${out}"
else
  red "  FAIL: pod was not rejected. Output: ${out}"
  $KUBECTL -n "$NS" delete pod too-big --ignore-not-found
  exit 1
fi

green "Phase 0 verified."
