# claude-code-pet

> A floating, animated, multi-session-aware pet overlay for [Claude Code](https://docs.claude.com/en/docs/claude-code) on macOS. Bring your own Codex-format spritesheet. Invoked as `/pet`.

## Why

I love Claude Code's new agent activity view — but I missed my Codex pet sitting in the corner reacting to what was happening. The Codex pet is rendered by Codex Desktop, and Claude Code is a CLI with no overlay surface. So I built this: a real macOS NSWindow overlay that hooks into Claude Code's lifecycle events, reads the same Codex spritesheet format (`hatch-pet`), and adds a few things I wanted that Codex's pet doesn't do.

## What makes it different

- **Multi-session aware.** One speech bubble per active Claude Code session, each chip-stamped with the session's *real* name (resolved authoritatively from `~/.claude/sessions/<pid>.json`) and showing the latest assistant narration extracted from the JSONL transcript — not the raw tool command, not your prompt. Works across any number of concurrent sessions on any cwd.
- **Theme + typography match.** Defaults inherit your live macOS appearance + accent color (`NSColor.controlAccentColor`) — change accent in System Settings, the badge follows. Bubble blur is `NSVisualEffectView` HUD material so light/dark adapts automatically. Run `/pet theme` to also pull the *active terminal's* profile colors and font (iTerm.app, Apple Terminal, Ghostty supported) — so the pet visually fits whatever shell you're in.
- **Dismissible bubbles.** Right-click any session bubble to dismiss it; it returns on the next state change for that session.
- **Click to control the pet.** Left-click cycles animated states (sleeping → idle → thinking → done → working → alert → resume). Right-click cycles frozen poses (sit, wave, sleep, look). The pet auto-wakes from any manual override when a new alert fires or when your first session activates.
- **Drag-direction-aware run.** Grab and drag the pet — she runs left or right matching the drag direction (rows 1/2). Hover the pet → trot (row 7).
- **Cross-Space, always on top.** Joins every Space including fullscreen apps. No Dock icon. No Accessibility prompts. No screen-recording permission.
- **Terminal-agnostic.** The overlay is a separate macOS process driven by Claude Code's hooks — not anything terminal-specific. Works in iTerm, Terminal.app, Ghostty, Warp, Alacritty, Kitty — anything that runs Claude Code.

## Requirements

- **macOS 14.0+** (Sonoma or newer — needed for native WebP via ImageIO)
- **Python 3** on the system path (`/usr/bin/python3` ships with every modern Mac)
- **A Codex-format pet spritesheet** — see [Getting a pet](#getting-a-pet) below

No Homebrew packages, no Xcode, no Accessibility/Screen-Recording prompts. The plugin creates an isolated venv on first run and installs `pyobjc-core` + `pyobjc-framework-Cocoa` into it.

## Install

```bash
# In any Claude Code session:
/plugin marketplace add ishaan-os/claude-code-pet
/plugin install pet@claude-code-pet
/pet start
```

That's it. The plugin registers its own lifecycle hooks — you do **not** need to edit `~/.claude/settings.json`. First `/pet start` bootstraps the venv, copies a default config, auto-detects your pet from `~/.codex/pets/`, and launches.

## Getting a pet

This plugin **does not ship a sprite asset**. You bring your own. The expected format is an 8×9 atlas of 192×208 pixel cells, saved as `spritesheet.webp` alongside a `pet.json` metadata file — the format produced by [OpenAI's Codex `hatch-pet` skill](https://github.com/openai/skills/tree/main/skills/.curated/hatch-pet).

The easiest way to get one is to install Codex and hatch a pet:

```bash
# Install Codex once: https://github.com/openai/codex
codex
> /hatch-pet
```

Codex generates the sprite and saves it to `~/.codex/pets/<pet-name>/`. The pet name is whatever you (or Codex) choose at hatch time — there is no canonical default. On the next `/pet start`, the launcher auto-detects any single spritesheet under `~/.codex/pets/*/` and wires it up. If you have multiple pets, it lists them and asks you to pick one in `~/.claude/pet/config.json`.

If you'd rather draw your own: any 8-column × 9-row WebP or PNG with 192×208 cells will work. Update the row→state mapping in `config.json` to match your animations (run `/pet previews` to dump one PNG per atlas row for reference).

## Usage

```text
/pet start         # wake the pet
/pet stop          # tuck the pet
/pet status        # check what's going on
/pet restart       # apply config.json changes
/pet state <name>  # force a state (idle, thinking, working, alert, done, sleeping)
/pet previews      # render one PNG per atlas row (for tuning)
/pet tune          # open config.json
/pet theme         # sync colors + font to the current terminal's profile
/pet switch <id>   # point at ~/.codex/pets/<id>/
```

Or just ask in natural language — "wake my pet", "tuck the pet in", "what state is the pet in?".

## How it works

```
┌────────────────────────────────────────────────────────────┐
│  Every Claude Code session (any cwd, any pid)              │
│  └─ Lifecycle hook fires (UserPromptSubmit, PreToolUse,    │
│      Stop, Notification, SessionEnd)                       │
│         │                                                  │
│         ▼                                                  │
│  ${CLAUDE_PLUGIN_ROOT}/bin/pet-state.sh <state>            │
│         │                                                  │
│         ▼                                                  │
│  pet-state.py: locked read-modify-write of                 │
│  ~/.claude/pet/state.json {sessions: {sid → {…}}}          │
│  + extracts latest assistant block from JSONL transcript   │
│  + resolves real session name from ~/.claude/sessions/     │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼ (mtime poll, 250 ms)
┌────────────────────────────────────────────────────────────┐
│  overlay.py: borderless transparent NSWindow at            │
│    NSStatusWindowLevel, canJoinAllSpaces | stationary |    │
│    fullScreenAuxiliary | ignoresCycle                      │
│  Renders one sprite cell + one HUD bubble per session.     │
│  Click / drag / hover via custom hit-testing.              │
└────────────────────────────────────────────────────────────┘
```

- **Plugin code** (read-only, ships with the plugin): `${CLAUDE_PLUGIN_ROOT}/{bin,src,hooks,commands}`
- **User state** (writable, per-user, auto-bootstrapped on first start):
  - `~/.claude/pet/venv/` — isolated pyobjc install
  - `~/.claude/pet/config.json` — your editable config
  - `~/.claude/pet/state.json` — aggregated state across all sessions
  - `~/.claude/pet/position.json` — last window position (auto-saved on drag)
  - `~/.claude/pet/overlay.{pid,log}` — process metadata

## Configuration

After the first `/pet start`, edit `~/.claude/pet/config.json`:

```json
{
  "spritesheet": "~/.codex/pets/<your-pet>/spritesheet.webp",
  "cellWidth": 192,
  "cellHeight": 208,
  "cols": 8,
  "rows": 9,
  "fps": 6.0,
  "scale": 0.42,
  "anchor": "bottom-right",
  "margin": 24,
  "themeAccent": "system",
  "doneAccent":  "system-green",
  "bubbleFontFamily": null,
  "chipFontFamily":   null,
  "bubbleFontSize":   13.0,
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

### Theme & typography

- `themeAccent` / `doneAccent` accept either a hex string (`"#1F8FB5"`) or a magic value that resolves to a live macOS system color:
  - `"system"` / `"system-accent"` → `NSColor.controlAccentColor` (whatever you picked in System Settings → Appearance)
  - `"system-green"` → `NSColor.systemGreenColor`
  - `"system-red"` → `NSColor.systemRedColor`
  - `"system-blue"` → `NSColor.systemBlueColor`
- `bubbleFontFamily` / `chipFontFamily` accept a PostScript font name (e.g. `"MesloLGS NF"`, `"JetBrainsMonoNFM-Regular"`). When `null` or absent, the macOS monospaced system font is used.
- Run `/pet theme` from inside your terminal to *auto-detect* and write theme + font values matching the active terminal profile:
  - **iTerm.app**: reads the active profile's ANSI cyan/green and Normal Font.
  - **Apple Terminal**: queries the selected tab via `osascript`.
  - **Ghostty**: parses `~/.config/ghostty/config` (or the `Application Support` variant).
  - **Anything else**: falls back to `themeAccent: "system"` + `doneAccent: "system-green"`.

### Hook → state mapping

| Claude Code hook    | State written  | Default animation (row) |
|---------------------|----------------|-------------------------|
| `UserPromptSubmit`  | `thinking`     | sitting-blink (6)       |
| `PreToolUse`        | `working`      | jumping (4)             |
| `Stop`              | `done`         | paw-wave (3)            |
| `Notification`      | `alert`        | paw-wave (3)            |
| `SessionEnd`        | `sleeping`     | curled (5)              |

These map to standard [Claude Code hook events](https://docs.claude.com/en/docs/claude-code/hooks) and are declared by the plugin in `hooks/hooks.json` — no `settings.json` editing required.

## Troubleshooting

**Pet doesn't appear after `/pet start`.** Check `~/.claude/pet/overlay.log`. The most common cause is no spritesheet at the configured path — the start script validates this and prints an explicit message.

**Pet appears off-screen.** macOS multi-monitor coordinates can be negative; if you dragged the pet to a monitor that's no longer connected, delete `~/.claude/pet/position.json` and restart.

**"Already running" with no visible pet.** Stale PID. `/pet stop` then `/pet start` (stop falls back to `pkill` if the PID file is stale).

**Bubble shows my prompt instead of Claude's reply.** The plugin extracts the latest assistant block from the JSONL transcript and falls back to the hook's `message` field on a miss. Check that `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` exists and is being written.

**Session name doesn't match my terminal title.** The plugin reads `~/.claude/sessions/<pid>.json → name`. If that file isn't being written by your Claude Code version, the plugin falls back to the first user message.

## Uninstall

```bash
/plugin uninstall pet@claude-code-pet
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
