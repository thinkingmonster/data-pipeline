#!/usr/bin/env bash
set -euo pipefail
PROFILE="${PROFILE:-data-pipeline}"
echo "This will DELETE minikube profile '${PROFILE}' and all its state."
read -rp "Continue? [y/N] " ans
[[ "${ans}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
minikube delete --profile "${PROFILE}"
