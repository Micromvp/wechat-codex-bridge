#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${WECHAT_BRIDGE_HOST:-127.0.0.1}"
PORT="${WECHAT_BRIDGE_PORT:-8787}"

echo "Starting WeChat bridge on http://${HOST}:${PORT}"
echo "Make sure WeChat is open and Accessibility permission is granted."
if [[ -n "${WECHAT_AGENT_COMMAND:-}" ]]; then
  echo "Agent command: ${WECHAT_AGENT_COMMAND}"
else
  echo "Agent command: mock agent (set WECHAT_AGENT_COMMAND to use Codex or OpenClaw)"
fi

exec python3 "$ROOT/scripts/wechat_bridge_service.py"
