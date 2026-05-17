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

sprite_raw=$("${PYTHON}" -c "import json,sys; c=json.load(open(sys.argv[1])); print(c.get('spritesheet','') or '')" "${USER_CONFIG}")
sprite=$("${PYTHON}" -c "import os,sys; print(os.path.expanduser(sys.argv[1]) if sys.argv[1] else '')" "${sprite_raw}")

# Auto-detect a Codex pet on first run when the config has no spritesheet.
if [[ -z "${sprite_raw}" ]]; then
    detected=()
    if [[ -d "${HOME}/.codex/pets" ]]; then
        while IFS= read -r -d '' f; do detected+=("$f"); done \
            < <(find "${HOME}/.codex/pets" -maxdepth 2 -name spritesheet.webp -print0 2>/dev/null)
    fi

    if [[ ${#detected[@]} -eq 1 ]]; then
        sprite="${detected[0]}"
        echo "claude-code-pet: auto-detected pet at ${sprite}"
        "${PYTHON}" -c "import json,sys; p=sys.argv[1]; s=sys.argv[2]; c=json.load(open(p)); c['spritesheet']=s; json.dump(c, open(p,'w'), indent=2)" "${USER_CONFIG}" "${sprite}"
    elif [[ ${#detected[@]} -gt 1 ]]; then
        echo "claude-code-pet: multiple pets found in ~/.codex/pets/. Pick one and set \"spritesheet\" in:" >&2
        echo "    ${USER_CONFIG}" >&2
        echo "Available:" >&2
        printf '    %s\n' "${detected[@]}" >&2
        exit 1
    else
        cat >&2 <<EOF
claude-code-pet: no Codex-format pet found.

You need a spritesheet (8×9 atlas, 192×208 cells). Two options:

  1. Hatch one with OpenAI's Codex CLI:
       codex
       > /hatch-pet
     The result lands at ~/.codex/pets/<your-pet>/spritesheet.webp.
     Re-run /pet:pet start and it will auto-detect.

  2. Supply your own WebP/PNG matching the atlas geometry. Set the
     absolute path in ${USER_CONFIG} → "spritesheet".

See the README for the full layout spec.
EOF
        exit 1
    fi
elif [[ ! -f "${sprite}" ]]; then
    echo "claude-code-pet: spritesheet not found at ${sprite}" >&2
    echo "Edit ${USER_CONFIG} → \"spritesheet\" to fix." >&2
    exit 1
fi

nohup "${PYTHON}" "${SCRIPT}" run >>"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
echo "pet started (pid $(cat "${PID_FILE}"))"
