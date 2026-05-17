#!/usr/bin/env python3
"""Claude Code hook bridge: read the hook JSON payload from stdin, extract a
short status label for the current session, and atomically merge it into
~/.claude/pet/state.json.

Each session also gets a `name` (Claude Code's session label — derived from
the first user message of its transcript) and `cwd`. The name is cached so
we don't re-parse the transcript on every hook fire.

state.json schema:
    {
      "sessions": {
        "<session_id>": {
          "state": "...", "label": "...", "name": "...",
          "cwd": "...", "ts": <epoch>
        },
        ...
      }
    }
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

PET_HOME = Path.home() / ".claude" / "pet"
STATE_PATH = PET_HOME / "state.json"
LOCK_PATH = PET_HOME / "state.lock"
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

MAX_LABEL = 110
MAX_NAME = 28
STALE_AFTER = 3600


def truncate(s: str, limit: int = MAX_LABEL) -> str:
    s = s.strip().replace("\n", " ")
    return (s[: limit - 1] + "…") if len(s) > limit else s


def _trailing(s: str, limit: int = MAX_LABEL) -> str:
    s = s.strip()
    if not s:
        return ""
    parts = [p.strip() for p in s.split("\n\n") if p.strip()]
    last = parts[-1] if parts else s
    last = " ".join(last.split())
    return (last[: limit - 1] + "…") if len(last) > limit else last


def find_transcript(session_id: str) -> str:
    """Fallback when the hook payload doesn't carry a usable transcript_path:
    scan ~/.claude/projects/*/<session_id>.jsonl for the matching file.
    """
    if not session_id:
        return ""
    try:
        for p in PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
            return str(p)
    except OSError:
        pass
    return ""


def latest_assistant_say(transcript_path: str) -> str:
    """Return the most recent assistant `text` or `thinking` block from the
    transcript JSONL. Text is preferred over thinking when both appear in the
    same message. Empty string if the transcript can't be read.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    last = ""
    try:
        with p.open("r", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                msg_text = ""
                msg_thinking = ""
                if isinstance(content, str):
                    msg_text = content
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        t = block.get("type")
                        if t == "text":
                            txt = str(block.get("text", ""))
                            if txt.strip():
                                msg_text = txt
                        elif t == "thinking":
                            txt = str(block.get("thinking", ""))
                            if txt.strip():
                                msg_thinking = txt
                chosen = msg_text or msg_thinking
                if chosen:
                    last = chosen
    except OSError:
        return ""
    return last


GENERIC_NOTIFICATIONS = {
    "claude is waiting for your input",
    "claude needs your input",
    "",
}


def _resolve_transcript(payload: dict) -> str:
    tx = str(payload.get("transcript_path") or "")
    if not tx or not Path(tx).is_file():
        tx = find_transcript(str(payload.get("session_id") or ""))
    return tx


def label_from_payload(payload: dict) -> str:
    ev = payload.get("hook_event_name", "")
    if ev in ("PreToolUse", "PostToolUse"):
        say = latest_assistant_say(_resolve_transcript(payload))
        if say:
            return _trailing(say)
        tool = payload.get("tool_name", "")
        inp = payload.get("tool_input") or {}
        detail = ""
        if isinstance(inp, dict):
            for key in ("file_path", "path", "command", "description", "url", "pattern", "query", "prompt", "subagent_type"):
                v = inp.get(key)
                if isinstance(v, str) and v:
                    detail = v
                    break
        if detail and len(detail) > 48:
            detail = "…" + detail[-47:]
        return truncate(f"{tool} · {detail}" if detail else tool)
    if ev == "Notification":
        say = latest_assistant_say(_resolve_transcript(payload))
        msg = str(payload.get("message", "")).strip()
        is_generic = msg.lower() in GENERIC_NOTIFICATIONS
        if say:
            question = _trailing(say, 80)
            if msg and not is_generic:
                action = msg if len(msg) <= 64 else msg[:63] + "…"
                return f"{question}\n→ {action}"
            return question
        return truncate(msg)
    return ""


def _first_text_block(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = str(block.get("text", ""))
                if t:
                    return t
    return ""


def claude_session_name(session_id: str) -> str:
    """Look up the official session name from Claude Code's per-PID session
    metadata files (~/.claude/sessions/<pid>.json keyed by sessionId).
    """
    if not session_id or not SESSIONS_DIR.is_dir():
        return ""
    try:
        for p in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("sessionId") != session_id:
                continue
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                stripped = name.strip()
                return (stripped[: MAX_NAME - 1] + "…") if len(stripped) > MAX_NAME else stripped
    except OSError:
        return ""
    return ""


def derive_session_name(transcript_path: str) -> str:
    """Find the first user-authored text in the transcript. Returns
    a truncated single-line summary, or empty string if unreadable.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    try:
        with p.open("r", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Claude Code transcripts vary slightly in shape; try both.
                msg = obj
                if obj.get("role") not in ("user", "assistant", "system"):
                    inner = obj.get("message")
                    if isinstance(inner, dict):
                        msg = inner
                if msg.get("role") != "user":
                    continue
                text = _first_text_block(msg).strip()
                if not text:
                    continue
                # Take first non-empty line so multi-line prompts collapse.
                first_line = next((ln for ln in text.split("\n") if ln.strip()), "").strip()
                if not first_line:
                    continue
                return (first_line[: MAX_NAME - 1] + "…") if len(first_line) > MAX_NAME else first_line
    except OSError:
        return ""
    return ""


def main() -> None:
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    label = label_from_payload(payload) if payload else ""
    session_id = str(payload.get("session_id") or "default")
    cwd = str(payload.get("cwd") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    now = time.time()

    PET_HOME.mkdir(parents=True, exist_ok=True)

    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                raw_state = STATE_PATH.read_text() if STATE_PATH.exists() else ""
                data = json.loads(raw_state) if raw_state.strip() else {}
            except (OSError, json.JSONDecodeError):
                data = {}
            sessions = data.get("sessions") if isinstance(data, dict) else None
            if not isinstance(sessions, dict):
                sessions = {}

            existing = sessions.get(session_id, {})
            existing_name = (
                existing.get("name", "") if isinstance(existing, dict) else ""
            )
            # Authoritative: Claude Code's own session metadata. Re-read each
            # time so renames (e.g. when Claude generates a summary later)
            # propagate. Fall back to a cached name or to the transcript's
            # first user message if Claude Code hasn't written one yet.
            official_name = claude_session_name(session_id)
            name = official_name or existing_name or derive_session_name(transcript_path)

            sessions[session_id] = {
                "state": state,
                "label": label,
                "name": name,
                "cwd": cwd,
                "ts": now,
            }

            cutoff = now - STALE_AFTER
            sessions = {
                sid: s for sid, s in sessions.items()
                if isinstance(s, dict) and s.get("ts", 0) >= cutoff
            }

            out = {"sessions": sessions}
            tmp = STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(out, indent=2))
            os.replace(tmp, STATE_PATH)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
