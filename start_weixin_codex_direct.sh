#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CODEX_CWD="${WEIXIN_CODEX_CWD:-${CODEX_CWD:-$ROOT}}"
printf -v QUOTED_CODEX_CWD '%q' "$CODEX_CWD"

if [[ -z "${WEIXIN_CODEX_COMMAND:-}" ]]; then
  export WEIXIN_CODEX_COMMAND="codex exec --json --cd ${QUOTED_CODEX_CWD} --sandbox workspace-write {prompt}"
fi
if [[ -z "${WEIXIN_CODEX_RESUME_COMMAND:-}" ]]; then
  export WEIXIN_CODEX_RESUME_COMMAND='codex exec resume --json {thread_id} {prompt}'
fi
export WEIXIN_CODEX_CWD="$CODEX_CWD"
export WEIXIN_CODEX_NATIVE_SESSION="${WEIXIN_CODEX_NATIVE_SESSION:-true}"
export WEIXIN_CODEX_HISTORY_TURNS="${WEIXIN_CODEX_HISTORY_TURNS:-20}"

echo "Starting direct WeChat ClawBot -> Codex bridge"
echo "Codex cwd: ${WEIXIN_CODEX_CWD}"
echo "Command: ${WEIXIN_CODEX_COMMAND}"
echo "Resume command: ${WEIXIN_CODEX_RESUME_COMMAND}"
echo "Native Codex session: ${WEIXIN_CODEX_NATIVE_SESSION}"
echo "History turns per WeChat user: ${WEIXIN_CODEX_HISTORY_TURNS}"

exec python3 "$ROOT/scripts/weixin_codex_direct.py"
