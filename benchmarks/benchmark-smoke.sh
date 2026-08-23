#!/usr/bin/env bash
# Quick smoke run of the SynthID-text benchmark: 2 docs, 1 seed, one variant.
# Results land in out/bench-smoke/ (override with OUT_DIR).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a
export MARKLLM_DIR="${MARKLLM_DIR:-$HOME/MarkLLM}"
export HF_HOME="${HF_HOME:-$ROOT/.hf-cache}"
exec python3 "$ROOT/service/scripts/bench_synthid_text.py" \
  --markllm-dir "$MARKLLM_DIR" \
  --docs 2 --seeds 1 --max-new-tokens 128 \
  --variants "paraphrase:1" \
  --out-dir "${OUT_DIR:-$ROOT/out/bench-smoke}" \
  --tag smoke
