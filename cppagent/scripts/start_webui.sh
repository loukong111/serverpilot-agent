#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x build/cpp_analyzer ]]; then
  cmake -S . -B build
  cmake --build build
fi

HOST="${PROJECTAGENTCPP_HOST:-127.0.0.1}"
PORT="${PROJECTAGENTCPP_PORT:-8765}"

echo "ProjectAgentCpp Web UI: http://${HOST}:${PORT}"
python3 webui/server.py --host "$HOST" --port "$PORT"
