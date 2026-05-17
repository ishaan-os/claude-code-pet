#!/usr/bin/env bash
# Start the floating pet overlay. Self-bootstraps the venv + config on first
# run. Idempotent: re-running while live just reports the existing PID.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "claude-code-pet: macOS only (detected $(uname -s))" >&2
    exit 1
fi

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PET_HOME="${HOME}/.claude/pet"
PID_FILE="${PET_HOME}/overlay.pid"
LOG_FILE="${PET_HOME}/overlay.log"
VENV="${PET_HOME}/venv"
PYTHON="${VENV}/bin/python"
SCRIPT="${PLUGIN_ROOT}/src/overlay.py"
DEFAULT_CONFIG="${PLUGIN_ROOT}/src/config.default.json"
USER_CONFIG="${PET_HOME}/config.json"

if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}")"
    if kill -0 "${pid}" 2>/dev/null; then
        echo "pet already running (pid ${pid})"
        exit 0
    fi
    rm -f "${PID_FILE}"
fi

mkdir -p "${PET_HOME}"

if [[ ! -f "${USER_CONFIG}" ]]; then
    cp "${DEFAULT_CONFIG}" "${USER_CONFIG}"
    echo "wrote default config → ${USER_CONFIG}"
fi

if [[ ! -x "${PYTHON}" ]]; then
    echo "claude-code-pet: creating venv at ${VENV} (one-time setup)..."
    /usr/bin/python3 -m venv "${VENV}"
    "${PYTHON}" -m pip install --quiet --upgrade pip
    echo "claude-code-pet: installing pyobjc (one-time setup)..."
    "${PYTHON}" -m pip install --quiet pyobjc-core pyobjc-framework-Cocoa
fi

sprite=$("${PYTHON}" - <<PY
import json, os, sys
with open("${USER_CONFIG}") as f:
    cfg = json.load(f)
print(os.path.expanduser(cfg.get("spritesheet", "")))
PY
)

if [[ ! -f "${sprite}" ]]; then
    cat >&2 <<EOF
claude-code-pet: spritesheet not found at:
    ${sprite}

You need a Codex-format pet spritesheet (8×9 atlas, 192×208 cells). Two paths:

  1. Generate one with OpenAI's Codex CLI:
       codex
       > /hatch-pet
     Then point ${USER_CONFIG} → "spritesheet" at the resulting
     ~/.codex/pets/<your-pet>/spritesheet.webp

  2. Supply your own WebP/PNG matching the atlas geometry and update the
     config.

See the README for the full layout spec.
EOF
    exit 1
fi

nohup "${PYTHON}" "${SCRIPT}" run >>"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
echo "pet started (pid $(cat "${PID_FILE}"))"
