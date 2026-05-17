#!/usr/bin/env bash
set -euo pipefail
PID_FILE="${HOME}/.claude/pet/overlay.pid"
if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}")"
    if kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}"
        echo "pet stopped (pid ${pid})"
    else
        echo "pet not running (stale pid ${pid})"
    fi
    rm -f "${PID_FILE}"
else
    pkill -f "/src/overlay.py run" 2>/dev/null || true
    echo "pet stopped"
fi
