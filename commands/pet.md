---
description: Manage the floating Codex-format pet overlay — start, stop, status, set state, render previews, tune, restart, or switch pets.
argument-hint: [start|stop|status|restart|state <name>|previews|tune|switch <id>]
---

The user invoked `/pet $ARGUMENTS`. Dispatch to the matching action below. If
`$ARGUMENTS` is empty, run the `status` branch.

The plugin code lives at `${CLAUDE_PLUGIN_ROOT}`. Per-user state lives at
`~/.claude/pet/`. The user must have already installed the plugin; the launcher
self-bootstraps the venv and a default config on first `start`.

## Subcommands

### `start`
Run `${CLAUDE_PLUGIN_ROOT}/bin/start.sh`. Idempotent — reports the existing PID
if the overlay is already running. On first run, this also creates the venv,
installs pyobjc, copies the default config, and auto-detects a Codex pet from
`~/.codex/pets/`.

### `stop`
Run `${CLAUDE_PLUGIN_ROOT}/bin/stop.sh`.

### `restart`
Run `stop` then `start`. Required after editing `~/.claude/pet/config.json`.

### `status`
Check whether `~/.claude/pet/overlay.pid` points at a live process (`kill -0`).
Then print the current state by running:
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py current
```
If anything looks wrong, tail the last 5 lines of `~/.claude/pet/overlay.log`.

### `state <name>`
Force the displayed animation directly (without triggering a hook event):
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py set-state <name>
```
Valid names come from `~/.claude/pet/config.json → states` (default: `idle`,
`thinking`, `working`, `alert`, `done`, `sleeping`).

### `previews`
Run:
```
~/.claude/pet/venv/bin/python ${CLAUDE_PLUGIN_ROOT}/src/overlay.py previews
```
Writes one PNG strip per atlas row to `~/.claude/pet/previews/`. Read each one
and report which row contains which animation so the user can update the
`states` mapping in `config.json` accordingly.

### `tune`
Open `~/.claude/pet/config.json` for the user to edit. Tunable fields:
- `fps` — animation speed (default 6)
- `scale` — render size as a fraction of native 192×208 (default 0.42)
- `anchor` — `bottom-right` | `bottom-left` | `top-right` | `top-left`
- `margin` — pixels from the chosen corner
- `themeAccent` — hex color for the badge while sessions are working
- `doneAccent` — hex color for the badge when alert/done
- `states` — `{name: {row, frames}}` row→animation mapping

Remind the user to run `/pet restart` to apply changes.

### `switch <pet-id>`
Update `~/.claude/pet/config.json → spritesheet` to
`~/.codex/pets/<pet-id>/spritesheet.webp`. Confirm the path exists first; if
not, list the available pets under `~/.codex/pets/`. After updating, restart
the overlay.

## Click & drag UX (already rendered)

These are user interactions with the live overlay — describe to the user if
they ask "how do I use the pet?":

- **Left-click** the pet → cycle through animated states (sleeping → idle →
  thinking → done → working → alert → resume).
- **Right-click** the pet → cycle through frozen poses (sit, wave, sleep,
  look, resume).
- **Drag** → run animation in the drag direction (rows 1 right / 2 left).
- **Hover** → trot (row 7).
- **Right-click a session bubble** → dismiss it. It reappears on the next
  state change for that session.
- Pet auto-wakes from any manual override on (a) a new `alert`, or (b) the
  first session activating from idle.
