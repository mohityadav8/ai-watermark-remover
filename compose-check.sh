#!/usr/bin/env bash
set -uo pipefail

# Validate the running compose stack. Minimal output; exit code only:
#   0 = every service passes, 1 = any service fails.
#
#   wr-core       -> HTTP /health must answer
#   harness/heavy -> one-shot CLIs must exit 0 on --help

BASE_URL="${WATERMARKS_SERVICE_URL:-http://127.0.0.1:8765}"
COMPOSE=(docker compose --profile harness --profile heavy)
FAIL=0

if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
  echo "wr-core: OK"
else
  echo "wr-core: FAIL (no /health at $BASE_URL)"
  FAIL=1
fi

for svc in wr-markllm wr-markdiffusion wr-ctrlregen wr-synthid; do
  if "${COMPOSE[@]}" run --rm "$svc" --help >/dev/null 2>&1; then
    echo "$svc: OK"
  else
    echo "$svc: FAIL"
    FAIL=1
  fi
done

exit "$FAIL"
