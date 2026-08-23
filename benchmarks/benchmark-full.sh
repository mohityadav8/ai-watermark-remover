#!/usr/bin/env bash
# Full SynthID-text benchmark: 8 docs x 3 seeds, three rewrite variants,
# re-stamp control. Results land in out/bench-full/ (override with OUT_DIR).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a
export MARKLLM_DIR="${MARKLLM_DIR:-$HOME/MarkLLM}"
export HF_HOME="${HF_HOME:-$ROOT/.hf-cache}"
exec python3 "$ROOT/service/scripts/bench_synthid_text.py" \
  --markllm-dir "$MARKLLM_DIR" \
  --docs 10 --seeds 3 \
  --variants "paraphrase:1,paraphrase:3,backtranslate:1" \
  --restamp-control \
  --out-dir "${OUT_DIR:-$ROOT/out/bench-full}" \
  --tag full
