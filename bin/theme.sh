#!/usr/bin/env bash
# Detect the current terminal's theme/typography and write into ~/.claude/pet/config.json.
set -euo pipefail
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PET_HOME="${HOME}/.claude/pet"
PYTHON="${PET_HOME}/venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
    echo "claude-code-pet: venv not found at ${PET_HOME}/venv. Run /pet start first." >&2
    exit 1
fi

exec "${PYTHON}" "${PLUGIN_ROOT}/src/theme_detect.py"
