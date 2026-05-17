---
name: pet
description: Manage the floating macOS pet overlay — start, stop, check status, regenerate row previews, set the displayed animation state, or tune the row-to-state mapping. Use when the user types /pet, asks to wake/tuck their pet, or wants to point the overlay at a different Codex pet folder.
---

# /pet — floating pet overlay for Claude Code

A native macOS overlay (PyObjC + AppKit) that renders an animation cell from a
Codex-format pet spritesheet in a borderless transparent always-on-top window.
The window joins all Spaces and stays visible above fullscreen apps. Animation
state is driven by Claude Code hooks writing to `~/.claude/pet/state.json`,
aggregated across **all** concurrent Claude Code sessions.

## Layout

Plugin code (read-only, ships with the plugin):
```
${CLAUDE_PLUGIN_ROOT}/
├── bin/start.sh, stop.sh, pet-state.sh
├── src/overlay.py, pet-state.py, config.default.json
├── hooks/hooks.json
└── skills/pet/SKILL.md
```

User state (writable, per-user — bootstrapped on first `/pet start`):
```
~/.claude/pet/
├── venv/              # isolated pyobjc install (auto-created)
├── config.json        # atlas geometry, fps, scale, anchor, row→state mapping
├── state.json         # current per-session animation state (hooks write here)
├── position.json      # last window position (auto-saved on drag)
├── overlay.pid        # PID of the running overlay
├── overlay.log        # stdout/stderr of the overlay
└── previews/          # one PNG strip per atlas row
```

The pet asset itself lives at `~/.codex/pets/<id>/spritesheet.webp` + `pet.json`
(format produced by OpenAI's `hatch-pet` skill). To switch pets, edit
`spritesheet` in `~/.claude/pet/config.json`.

## Hook → state mapping (declared by the plugin)

| Claude Code hook    | State written  | Default row → animation |
|---------------------|----------------|-------------------------|
| `UserPromptSubmit`  | `thinking`     | row 6, sitting-blink    |
| `PreToolUse`        | `working`      | row 4, jumping          |
| `Stop`              | `done`         | row 3, paw-wave         |
| `Notification`      | `alert`        | row 3, paw-wave         |
| `SessionEnd`        | `sleeping`     | row 5, curled           |

## Subcommands

When the user types `/pet <verb>`, run the matching action.

### `/pet start`
Run `${CLAUDE_PLUGIN_ROOT}/bin/start.sh`. Idempotent — self-bootstraps the venv
and copies the default config on first run.

### `/pet stop`
Run `${CLAUDE_PLUGIN_ROOT}/bin/stop.sh`.

### `/pet status`
Report whether `~/.claude/pet/overlay.pid` points at a live process and print
the current state by running:
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py current
```
Tail last 5 lines of `~/.claude/pet/overlay.log` if there are errors.

### `/pet state <name>`
Set the displayed animation directly (without triggering a hook event):
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py set-state <name>
```
Valid names come from `config.json → states` (default: `idle`, `thinking`,
`working`, `alert`, `done`, `sleeping`).

### `/pet previews`
Run:
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py previews
```
Writes one PNG per atlas row to `~/.claude/pet/previews/`. Read each one and
report which row contains which animation so the user can update
`config.json → states` accordingly.

### `/pet tune`
Open `~/.claude/pet/config.json` for editing. Adjust:
- `fps` — animation speed (default 6)
- `scale` — render size (default 0.42 of native 192×208)
- `anchor` — `bottom-right` | `bottom-left` | `top-right` | `top-left`
- `margin` — pixels from the chosen corner
- `themeAccent` — hex color for the badge while sessions are active
- `doneAccent` — hex color for the badge when alert/done
- `states` — `{name: {row, frames}}`. After saving, restart with
  `/pet restart` so the new geometry takes effect.

### `/pet restart`
Stop then start. Required after editing `config.json`.

### `/pet switch <pet-id>`
Update `~/.claude/pet/config.json → spritesheet` to
`~/.codex/pets/<pet-id>/spritesheet.webp`, then restart. Confirm the path
exists before writing.

## Click & drag UX (rendered overlay)

- **Left-click** the pet → cycle through animated states (sleeping → idle →
  thinking → done → working → alert → resume normal).
- **Right-click** the pet → cycle through static frozen poses (sit, wave,
  sleep, look, resume normal).
- **Drag** → run animation in the drag direction (rows 1 right / 2 left).
- **Hover** → trot (row 7).
- **Right-click a session bubble** → dismiss it (it reappears on next state
  change).
- Pet wakes from any manual override on (a) new `alert`, or (b) 0→1+ active
  session transition.

## Implementation notes

- The overlay polls `state.json` mtime every 250 ms; no IPC. Atomic writes via
  `os.replace` + `fcntl.flock`.
- Window properties: `NSStatusWindowLevel`, collection behavior =
  `canJoinAllSpaces | stationary | fullScreenAuxiliary | ignoresCycle`,
  transparent, no shadow.
- Activation policy `Accessory` so no Dock icon.
- macOS reads WebP natively via ImageIO (14.0+).
- Session names resolved authoritatively from `~/.claude/sessions/<pid>.json`;
  assistant narration extracted from the JSONL transcript under
  `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`.
