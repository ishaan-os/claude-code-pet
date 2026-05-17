# claude-code-pet

> A floating, animated, multi-session-aware pet overlay for [Claude Code](https://docs.claude.com/en/docs/claude-code) on macOS. Bring your own Codex-format spritesheet.

`claude-code-pet` puts a tiny animated companion in the corner of your screen who reacts to what Claude Code is doing — thinking, working, waiting on you, or asleep. When you have multiple Claude Code sessions running at once, each gets its own speech bubble showing the latest assistant narration and the real session name.

It's a native macOS overlay (PyObjC + AppKit) — borderless, transparent, always-on-top, and joins every Space including fullscreen apps.

> **Status:** v0.1 — opinionated, macOS-only, "works on my machine but probably yours too." Open an issue if it doesn't.

## Requirements

- **macOS 14.0+** (Sonoma or newer — needed for native WebP via ImageIO)
- **Python 3** on the system path (`/usr/bin/python3` works on every modern Mac)
- **A Codex-format pet spritesheet** — see [Getting a pet](#getting-a-pet) below

That's it. No Homebrew packages, no Xcode, no Accessibility permissions. The plugin creates an isolated venv on first run and installs `pyobjc-core` + `pyobjc-framework-Cocoa` into it.

## Install

```bash
# In any Claude Code session:
/plugin marketplace add ishaan-os/claude-code-pet
/plugin install claude-code-pet@claude-code-pet
```

That's it. The plugin registers its own hooks — you do **not** need to edit `~/.claude/settings.json`.

## Getting a pet

This plugin **does not ship a pet sprite**. You bring your own. The expected format is the one produced by [OpenAI's Codex `hatch-pet` skill](https://github.com/openai/skills/tree/main/skills/.curated/hatch-pet): an 8×9 atlas of 192×208 pixel cells, saved as `spritesheet.webp` alongside a `pet.json` metadata file.

The easiest way to get one is to install Codex and hatch a pet:

```bash
# Install Codex once: https://github.com/openai/codex
codex
> /hatch-pet
```

Codex will generate the sprite and save it to `~/.codex/pets/<your-pet-name>/`. The default config in this plugin already looks for `~/.codex/pets/nola/spritesheet.webp` — if that's where yours landed, you're done. Otherwise point `~/.claude/pet/config.json → "spritesheet"` at the right path.

If you'd rather draw your own: any 8-column × 9-row WebP or PNG with 192×208 cells will work. Update the row→state mapping in `config.json` to match your animations.

## Usage

Once installed and a sprite is wired up:

```text
/claude-code-pet:pet start      # wake the pet
/claude-code-pet:pet stop       # tuck the pet
/claude-code-pet:pet status     # check what's going on
/claude-code-pet:pet restart    # apply config.json changes
/claude-code-pet:pet state <n>  # force a state (idle, thinking, working, alert, done, sleeping)
/claude-code-pet:pet previews   # render one PNG per atlas row (for tuning)
/claude-code-pet:pet tune       # open config.json
/claude-code-pet:pet switch <id># point at ~/.codex/pets/<id>/
```

Or just ask in natural language — "wake my pet", "tuck the pet in", "what state is the pet in?".

### Direct interaction with the pet

- **Left-click** the pet → cycle through animated states.
- **Right-click** the pet → cycle through frozen poses (sit, wave, sleep, look).
- **Drag** → the pet runs in the direction you drag.
- **Hover** → trot.
- **Right-click a session bubble** → dismiss it (returns on the next state change).
- The pet wakes from any manual override when a new alert fires or when the first session activates.

## How it works

```
┌──────────────────────────────────────────────────────────┐
│  Every Claude Code session (any cwd, any pid)            │
│  └─ Lifecycle hook fires (UserPromptSubmit, PreToolUse,  │
│      Stop, Notification, SessionEnd)                     │
│         │                                                │
│         ▼                                                │
│  ${CLAUDE_PLUGIN_ROOT}/bin/pet-state.sh <state>          │
│         │                                                │
│         ▼                                                │
│  pet-state.py: locked read-modify-write of               │
│  ~/.claude/pet/state.json {sessions: {sid → {...}}}      │
└──────────────────────────────────────────────────────────┘
                          │
                          ▼ (mtime poll, 250 ms)
┌──────────────────────────────────────────────────────────┐
│  overlay.py: NSWindow at NSStatusWindowLevel             │
│    canJoinAllSpaces | stationary | fullScreenAuxiliary   │
│  Renders one sprite cell, one bubble per active session  │
└──────────────────────────────────────────────────────────┘
```

- **Plugin code** (read-only, ships with the plugin): `${CLAUDE_PLUGIN_ROOT}/{bin,src,hooks,skills}`
- **User state** (writable, per-user, auto-bootstrapped on first start):
  - `~/.claude/pet/venv/` — isolated pyobjc install
  - `~/.claude/pet/config.json` — your editable config
  - `~/.claude/pet/state.json` — aggregated state across all sessions
  - `~/.claude/pet/position.json` — last window position
  - `~/.claude/pet/overlay.{pid,log}` — process metadata

Session names are resolved authoritatively from `~/.claude/sessions/<pid>.json`. Bubble text is extracted from the latest assistant message in the session's JSONL transcript under `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — falls back to the hook's `message` field on a miss.

## Configuration

After the first `/pet start`, edit `~/.claude/pet/config.json`:

```json
{
  "spritesheet": "~/.codex/pets/nola/spritesheet.webp",
  "cellWidth": 192,
  "cellHeight": 208,
  "cols": 8,
  "rows": 9,
  "fps": 6.0,
  "scale": 0.42,
  "anchor": "bottom-right",
  "margin": 24,
  "themeAccent": "#1F8FB5",
  "doneAccent":  "#34C759",
  "states": {
    "idle":     {"row": 0, "frames": 6},
    "thinking": {"row": 6, "frames": 6},
    "working":  {"row": 4, "frames": 5},
    "alert":    {"row": 3, "frames": 4},
    "done":     {"row": 3, "frames": 4},
    "sleeping": {"row": 5, "frames": 8}
  }
}
```

Run `/pet previews` to see what's in each row of your spritesheet, then update the `states` mapping to taste. `/pet restart` to apply.

## Hook → state mapping

| Claude Code hook    | State written  | Default animation (row) |
|---------------------|----------------|-------------------------|
| `UserPromptSubmit`  | `thinking`     | sitting-blink (6)       |
| `PreToolUse`        | `working`      | jumping (4)             |
| `Stop`              | `done`         | paw-wave (3)            |
| `Notification`      | `alert`        | paw-wave (3)            |
| `SessionEnd`        | `sleeping`     | curled (5)              |

These map to standard [Claude Code hook events](https://docs.claude.com/en/docs/claude-code/hooks) and are declared by the plugin in `hooks/hooks.json` — no `settings.json` editing required.

## Troubleshooting

**Pet doesn't appear after `/pet start`.** Check `~/.claude/pet/overlay.log`. The most common cause is a missing or wrong-path spritesheet — the start script validates this and prints an explicit message.

**Pet appears off-screen.** macOS multi-monitor coordinates can be negative; if you dragged the pet to a monitor that's no longer connected, delete `~/.claude/pet/position.json` and restart.

**Multiple pets, or "already running" with no visible pet.** Stale PID. Run `/pet stop` (which falls back to `pkill -f overlay.py run` if the PID file is stale) and then `/pet start`.

**Bubble shows my prompt text instead of Claude's reply.** The plugin extracts the latest assistant block from the JSONL transcript. If extraction fails it falls back to the hook's `message`. Check that `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` exists and is being written.

**Session name doesn't match my terminal title.** The plugin reads `~/.claude/sessions/<pid>.json → name`. If that file isn't being written by your Claude Code version, the plugin falls back to the first user message.

## Uninstall

```bash
/plugin uninstall claude-code-pet@claude-code-pet
rm -rf ~/.claude/pet
```

The plugin uninstall removes the code and unregisters hooks. The `rm -rf` removes the per-user state directory (venv, config, position, logs).

## Legal & credits

This is a third-party Claude Code plugin. **Not affiliated with, endorsed by, or sponsored by Anthropic or OpenAI.**

- The spritesheet format originated in [OpenAI's `hatch-pet` skill](https://github.com/openai/skills/tree/main/skills/.curated/hatch-pet) (Apache-2.0).
- Pet sprites you generate via Codex are owned by you under [OpenAI's Terms of Use](https://openai.com/policies/row-terms-of-use/). This plugin only reads your own local files — it ships zero Codex code and zero sprite assets.
- "Claude" and "Claude Code" are trademarks of Anthropic. This plugin uses those names descriptively to indicate compatibility.
- "Codex" is a trademark of OpenAI. Used here nominatively to identify the upstream sprite format.

## License

MIT — see [LICENSE](LICENSE).
