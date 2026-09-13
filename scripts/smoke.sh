#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python "$ROOT/scripts/run_panel.py" --models mockllm/model --answer-format letter --max-tokens 64 "$@"
