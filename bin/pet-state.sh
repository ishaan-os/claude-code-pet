#!/usr/bin/env bash
# Hook wrapper: forwards the hook's stdin payload + state arg to pet-state.py.
# Silent on failure — must never block a Claude Code lifecycle event.
set -eu
PET_HOME="${HOME}/.claude/pet"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
HELPER="${PLUGIN_ROOT}/src/pet-state.py"
[[ -x "${PET_HOME}/venv/bin/python" && -f "${HELPER}" ]] || exit 0
"${PET_HOME}/venv/bin/python" "${HELPER}" "$@" >/dev/null 2>&1 || true
