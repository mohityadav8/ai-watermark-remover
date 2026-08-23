#!/usr/bin/env bash
set -euo pipefail

# Bootstrap an external THU-BPM/MarkLLM checkout for the optional text-watermark
# harness.
#
# The upstream project (https://github.com/THU-BPM/MarkLLM) is Apache-2.0 and
# is NOT bundled in this repository. This script clones it locally (pinned
# commit), creates a venv, and installs only the dependencies needed by
# detect_text_watermark.py (torch, transformers, datasets, ...).
#
# The base scoring model (default facebook/opt-1.3b) is downloaded from
# Hugging Face by detect_text_watermark.py at runtime, not here.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_DIR="${MARKLLM_DIR:-$HOME/MarkLLM}"
DIR=""
# Pinned upstream commit (2026-07-10). Do not point at a moving branch.
REF="c45ddc40f7b761beabe55a1b8dc4690e531d1c6d"
PYTHON="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage: setup_markllm.sh [--dir PATH] [--ref REF] [--python PYTHON]

Clones (if needed) THU-BPM/MarkLLM, creates a venv, and installs the Python
dependencies required by detect_text_watermark.py (including torch).

Options:
  --dir PATH     checkout directory (default: $MARKLLM_DIR or ~/MarkLLM)
  --ref REF      git ref to checkout (default: pinned commit SHA)
  --python PY    Python interpreter used to create the venv (default: python3)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      DIR="${2:?--dir requires a value}"
      shift 2
      ;;
    --ref)
      REF="${2:?--ref requires a value}"
      shift 2
      ;;
    --python)
      PYTHON="${2:?--python requires a value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

DIR="${DIR:-$DEFAULT_DIR}"
mkdir -p "$(dirname "$DIR")"
if realpath -m . >/dev/null 2>&1; then  # BSD/macOS realpath has no -m
  DIR="$(realpath -m "$DIR")"
else
  DIR="$(cd "$(dirname "$DIR")" && pwd)/$(basename "$DIR")"
fi

if [[ ! -d "$DIR/.git" ]]; then
  echo "Cloning THU-BPM/MarkLLM into $DIR (pinned ref: $REF)"
  git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/THU-BPM/MarkLLM.git "$DIR"
  git -C "$DIR" fetch --depth 1 origin "$REF"
  git -C "$DIR" checkout --detach "$REF"
  git -C "$DIR" sparse-checkout set --no-cone \
    '/watermark/' \
    '/config/' \
    '/utils/' \
    '/exceptions/' \
    '/visualize/' \
    '/evaluation/dataset.py' \
    '/LICENSE' \
    '/README.md'
  HEAD_SHA="$(git -C "$DIR" rev-parse HEAD)"
  if [[ "$HEAD_SHA" != "$REF" ]]; then
    echo "error: expected pinned ref $REF, got $HEAD_SHA" >&2
    exit 1
  fi
else
  echo "Using existing checkout: $DIR"
fi

if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  echo "Creating venv at $DIR/.venv"
  "$PYTHON" -m venv "$DIR/.venv"
fi

echo "Installing Python dependencies"
# Pin pip itself (unpinned --upgrade pip was a supply-chain drift point).
"$DIR/.venv/bin/python" -m pip install --upgrade "pip==26.2.1"

# Install torch with the right platform index before the other pinned deps.
if command -v nvidia-smi >/dev/null 2>&1; then
  cuda="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]*\.[0-9]*\).*/\1/p' | head -1)"
  if [[ -n "$cuda" ]]; then
    tag="cu${cuda/./}"
    index="https://download.pytorch.org/whl/$tag"
    echo "NVIDIA GPU detected (CUDA $cuda); installing torch from $index"
    "$DIR/.venv/bin/python" -m pip install torch --index-url "$index"
  else
    echo "nvidia-smi present but no CUDA version found; installing default torch"
    "$DIR/.venv/bin/python" -m pip install torch
  fi
else
  echo "No NVIDIA GPU detected; installing default torch (CPU/MPS)"
  "$DIR/.venv/bin/python" -m pip install torch
fi

"$DIR/.venv/bin/python" -m pip install -r "$SCRIPT_DIR/requirements-markllm.txt"

cat <<EOF

Done. Detect a scheme watermark in text with:

  export MARKLLM_DIR="$DIR"
  "$DIR/.venv/bin/python" "\$REPO/service/scripts/detect_text_watermark.py" detect TEXT --scheme kgw

The base scoring model (default facebook/opt-1.3b) is downloaded from
Hugging Face on first run. Detection is only valid against the SAME scheme
config + keys used at generation: this is a verification harness, not a
vendor-detector oracle.
EOF
