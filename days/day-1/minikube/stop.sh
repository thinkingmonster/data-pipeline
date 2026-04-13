#!/usr/bin/env bash
set -euo pipefail
PROFILE="${PROFILE:-data-pipeline}"
minikube stop --profile "${PROFILE}"
