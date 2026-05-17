#!/usr/bin/env python3
"""Floating pet overlay for Claude Code.

Renders a Codex-format pet (pet.json + spritesheet.webp) in a borderless
transparent always-on-top window. Nola is always visible. Three display
modes:

  * Idle               — no active sessions; sprite alone.
  * Expanded (bubbles) — count > 0 and not collapsed; one HUD-blur speech
                         bubble per active session, stacked above Nola and
                         right-aligned to where the badge would sit.
                         Each bubble has a teal "chat name" chip with the
                         session's cwd basename, then the activity label.
  * Collapsed (badge)  — count > 0 and collapsed; a small filled circular
                         badge in Nola's bottom-right corner. Color: green
                         when any session needs attention or just finished,
                         the configured theme accent otherwise.

state.json schema:
    {
      "sessions": {
        "<session_id>": {
          "state": "thinking|working|alert|done|idle|sleeping",
          "label": "...",
          "cwd": "/Users/...",
          "ts": <epoch>
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
CODEX_HOME = Path.home() / ".codex"
STATE_PATH = PET_HOME / "state.json"
CONFIG_PATH = PET_HOME / "config.json"
POSITION_PATH = PET_HOME / "position.json"
LOCK_PATH = PET_HOME / "state.lock"

ACTIVE_WINDOWS = {
    "thinking": 600,
    "working": 600,
    "alert": 30,  # wave for 30s after Notification, then derive back to idle/active
    "done": 30,   # wave for 30s after Stop, then derive back to idle/active
}
SPRITE_STATE_PRIORITY = ("alert", "working", "thinking", "done")
ATTENTION_STATES = {"alert", "done"}

# When Nola is in the "alert" state we don't loop a single animation at full
# speed — that's exhausting. Instead, cycle through a small playlist with an
# idle hold between each. At ALERT_TICK_DIV=2 the global animation tick (6fps)
# advances the sequence at ~3fps, so the wave plays for ~1.3s, then she rests
# ~2.7s, then winks, rests, then bounds, rests, then repeats (~12s cycle).
ALERT_SEQUENCE = [
    {"row": 3, "frames": 4, "hold_frames": 5},   # paw-wave
    {"row": 6, "frames": 6, "hold_frames": 5},   # sitting-blink / wink
    {"row": 4, "frames": 5, "hold_frames": 6},   # little bound / jump
]
ALERT_TICK_DIV = 1

# Per-state frame divisor for the global timer — higher = slower animation.
LONG_PRESS_SEC = 0.45

# Single-click cycle through animated sprite states. None at the end means
# "clear manual override" — fall back to whatever the live derived state is.
MANUAL_CYCLE = ["sleeping", "idle", "thinking", "done", "working", "alert", None]

# Long-press cycle through static (frozen) single-frame poses. Each entry is
# (label, row, col); None resets to live. Each press freezes Nola on a single
# spritesheet frame with no animation until the next session action.
STILL_POSES = [
    ("sit",   0, 0),
    ("wave",  3, 1),
    ("sleep", 5, 4),
    ("look",  0, 4),
    None,
]

# Per-state animation: loop the row at the global fps. Bump `div` to slow
# a row down. Set `holdFrames` to hold on the first frame (a still rest
# pose) for N global ticks after each animation cycle — useful for waves
# that would otherwise look frenetic if looped without pause. With fps=6,
# `frames=4` + `holdFrames=26` = one 4-frame wave every 30 ticks ≈ 5s.
STATE_ANIMATION = {
    "idle":     {"row": 0, "frames": 6, "div": 2},
    "thinking": {"row": 6, "frames": 6, "div": 1},
    "working":  {"row": 4, "frames": 5, "div": 1},
    "alert":    {"row": 3, "frames": 4, "div": 1, "holdFrames": 26},
    "done":     {"row": 3, "frames": 4, "div": 1, "holdFrames": 26},
    "sleeping": {"row": 5, "frames": 8, "div": 2},
}


def alert_row_col(frame_idx: int) -> tuple[int, int]:
    """Map the running tick counter to (row, col) for the alert sequence."""
    f = frame_idx // ALERT_TICK_DIV
    total = sum(a["frames"] + a["hold_frames"] for a in ALERT_SEQUENCE)
    if total == 0:
        return (0, 0)
    f = f % total
    for anim in ALERT_SEQUENCE:
        if f < anim["frames"]:
            return (anim["row"], f)
        f -= anim["frames"]
        if f < anim["hold_frames"]:
            return (0, 0)  # rest on the idle pose
        f -= anim["hold_frames"]
    return (0, 0)
PLACEHOLDER_LABELS = {
    "thinking": "Thinking…",
    "working": "Working…",
    "alert": "Needs your input",
    "done": "Done",
}
MAX_BUBBLES = 5

DEFAULT_CONFIG = {
    "spritesheet": str(CODEX_HOME / "pets" / "nola" / "spritesheet.webp"),
    "cellWidth": 192,
    "cellHeight": 208,
    "cols": 8,
    "rows": 9,
    "fps": 6.0,
    "scale": 0.5,
    "anchor": "bottom-right",
    "margin": 24,
    "bubbleMaxWidth": 260,
    "bubbleMaxHeight": 88,
    "bubbleFontSize": 13.0,
    "badgeDiameter": 24.0,
    "badgeOverlap": 0.62,
    "themeAccent": "system",
    "doneAccent": "system-green",
    "states": {
        "idle":     {"row": 0, "frames": 6},
        "thinking": {"row": 6, "frames": 6},
        "working":  {"row": 1, "frames": 8},
        "alert":    {"row": 3, "frames": 4},
        "done":     {"row": 3, "frames": 4},
        "sleeping": {"row": 5, "frames": 8},
    },
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in user.items() if k != "states"})
            if isinstance(user.get("states"), dict):
                cfg["states"] = user["states"]
        except (OSError, json.JSONDecodeError) as e:
            sys.stderr.write(f"pet: bad config.json ({e}); using defaults\n")
    cfg["spritesheet"] = os.path.expanduser(cfg["spritesheet"])
    return cfg


def read_sessions() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    return sessions if isinstance(sessions, dict) else {}


def short_name(name: str, cwd: str, session_id: str) -> str:
    if name:
        return name
    if cwd:
        base = os.path.basename(cwd.rstrip("/"))
        if base:
            return base
    return (session_id[:8] if session_id else "session")


def derive_view(sessions: dict, now: float) -> tuple[str, list[dict], int, str]:
    """Returns (sprite_state, entries, count, badge_class).

    entries: list of {"name": str, "label": str, "state": str} per active
             session, newest first (capped at MAX_BUBBLES + overflow).
    badge_class: "attention" if any session is in alert|done, else "working".
    """
    active_pairs = []
    for sid, s in sessions.items():
        if not isinstance(s, dict):
            continue
        st = s.get("state")
        if st not in ACTIVE_WINDOWS:
            continue
        if (now - float(s.get("ts", 0))) >= ACTIVE_WINDOWS[st]:
            continue
        active_pairs.append((sid, s))

    if not active_pairs:
        return "idle", [], 0, "working"

    active_pairs.sort(key=lambda p: float(p[1].get("ts", 0)), reverse=True)

    present_states = {s.get("state") for _, s in active_pairs}
    sprite_state = "idle"
    for pri in SPRITE_STATE_PRIORITY:
        if pri in present_states:
            sprite_state = pri
            break

    badge_class = "attention" if (present_states & ATTENTION_STATES) else "working"

    entries: list[dict] = []
    for sid, s in active_pairs[:MAX_BUBBLES]:
        text = (s.get("label") or "").strip()
        if not text:
            text = PLACEHOLDER_LABELS.get(s.get("state", ""), s.get("state", ""))
        entries.append({
            "session_id": sid,
            "name": short_name(
                str(s.get("name") or ""),
                str(s.get("cwd") or ""),
                sid,
            ),
            "label": text,
            "state": s.get("state", ""),
        })
    if len(active_pairs) > MAX_BUBBLES:
        entries.append({
            "name": "",
            "label": f"+{len(active_pairs) - MAX_BUBBLES} more sessions",
            "state": "",
        })

    return sprite_state, entries, len(active_pairs), badge_class


def write_manual_state(name: str) -> None:
    PET_HOME.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                raw = STATE_PATH.read_text() if STATE_PATH.exists() else ""
                data = json.loads(raw) if raw.strip() else {}
            except (OSError, json.JSONDecodeError):
                data = {}
            sessions = data.get("sessions") if isinstance(data, dict) else None
            if not isinstance(sessions, dict):
                sessions = {}
            sessions["manual"] = {"state": name, "label": "", "cwd": str(Path.cwd()), "ts": now}
            out = {"sessions": sessions}
            tmp = STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(out, indent=2))
            os.replace(tmp, STATE_PATH)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load_position() -> tuple[float, float] | None:
    try:
        obj = json.loads(POSITION_PATH.read_text())
        return float(obj["x"]), float(obj["y"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def save_position(x: float, y: float) -> None:
    PET_HOME.mkdir(parents=True, exist_ok=True)
    tmp = POSITION_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"x": x, "y": y}, indent=2))
    os.replace(tmp, POSITION_PATH)


def _hex_rgb(s: str, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        s = s.lstrip("#")
        if len(s) != 6:
            return fallback
        return (
            int(s[0:2], 16) / 255.0,
            int(s[2:4], 16) / 255.0,
            int(s[4:6], 16) / 255.0,
        )
    except ValueError:
        return fallback


# Magic config values that map to a live AppKit system color rather than a
# fixed hex. These follow macOS appearance + accent automatically.
_SYSTEM_COLOR_NAMES = {"system", "system-accent", "system-green", "system-red", "system-blue"}


def _resolve_color(value, fallback_hex: str, fallback_rgb: tuple[float, float, float]):
    """Returns an NSColor, resolving 'system*' magic values via AppKit."""
    from AppKit import NSColor

    if isinstance(value, str) and value.lower() in _SYSTEM_COLOR_NAMES:
        v = value.lower()
        if v in ("system", "system-accent"):
            return NSColor.controlAccentColor()
        if v == "system-green":
            return NSColor.systemGreenColor()
        if v == "system-red":
            return NSColor.systemRedColor()
        if v == "system-blue":
            return NSColor.systemBlueColor()
    rgb = _hex_rgb(value if isinstance(value, str) else fallback_hex, fallback_rgb)
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, 1.0)


def run_overlay() -> None:
    import objc
    from AppKit import (
        NSApplication, NSApplicationActivationPolicyAccessory,
        NSAttributedString, NSBackingStoreBuffered, NSBezierPath, NSColor,
        NSCompositingOperationSourceOver, NSEvent, NSFont, NSFontAttributeName,
        NSFontWeightBold, NSFontWeightMedium, NSFontWeightRegular,
        NSForegroundColorAttributeName, NSImage, NSLineBreakByTruncatingTail,
        NSLineBreakByWordWrapping, NSObject, NSParagraphStyleAttributeName,
        NSRect, NSRectFill, NSScreen, NSStatusWindowLevel,
        NSStringDrawingUsesFontLeading, NSStringDrawingUsesLineFragmentOrigin,
        NSTextAlignmentCenter, NSTextAlignmentLeft, NSTextField,
        NSTrackingActiveAlways, NSTrackingArea, NSTrackingInVisibleRect,
        NSTrackingMouseEnteredAndExited, NSView,
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialHUDWindow, NSVisualEffectStateActive,
        NSVisualEffectView, NSWindow,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorIgnoresCycle,
        NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    )
    from Foundation import (
        NSMakePoint, NSMakeRect, NSMakeSize, NSMutableParagraphStyle,
        NSPointInRect, NSTimer,
    )

    cfg = load_config()
    sprite_path = cfg["spritesheet"]
    sprite = NSImage.alloc().initWithContentsOfFile_(sprite_path)
    if sprite is None:
        sys.stderr.write(f"pet: failed to load spritesheet at {sprite_path}\n")
        sys.exit(2)

    sprite_w = float(cfg["cellWidth"]) * float(cfg["scale"])
    sprite_h = float(cfg["cellHeight"]) * float(cfg["scale"])
    bubble_max_w = float(cfg.get("bubbleMaxWidth", 260))
    bubble_max_h = float(cfg.get("bubbleMaxHeight", 88))
    body_font_size = float(cfg.get("bubbleFontSize", 13.0))
    badge_d = float(cfg.get("badgeDiameter", 24.0))
    badge_overlap = float(cfg.get("badgeOverlap", 0.62))
    badge_protrude = badge_d * (1.0 - badge_overlap)

    theme_color = _resolve_color(
        cfg.get("themeAccent", "system"), "#1F8FB5", (0.12, 0.56, 0.71)
    )
    done_color = _resolve_color(
        cfg.get("doneAccent", "system-green"), "#34C759", (0.20, 0.78, 0.35)
    )

    bubble_outer_pad = 10.0
    chip_pad_x = 7.0
    chip_pad_y = 3.0
    chip_inset = 12.0  # how far the chip sits from the bubble's outer edge
    inter_bubble_gap = 14.0  # leave room for chip overhang between bubbles
    bubble_corner = 12.0
    drag_threshold_px2 = 16.0

    def with_rounded_design(font, size):
        try:
            desc = font.fontDescriptor().fontDescriptorWithDesign_("rounded")
            cand = NSFont.fontWithDescriptor_size_(desc, size)
            if cand is not None:
                return cand
        except Exception:
            pass
        return font

    def _named_or(default_font, family_key, size, weight):
        family = cfg.get(family_key)
        if family:
            cand = NSFont.fontWithName_size_(str(family), size)
            if cand is not None:
                return cand
        return default_font

    body_font = _named_or(
        NSFont.monospacedSystemFontOfSize_weight_(body_font_size, NSFontWeightRegular),
        "bubbleFontFamily", body_font_size, NSFontWeightRegular,
    )
    chip_font = _named_or(
        NSFont.monospacedSystemFontOfSize_weight_(11.0, NSFontWeightRegular),
        "chipFontFamily", 11.0, NSFontWeightRegular,
    )
    badge_font = with_rounded_design(
        NSFont.systemFontOfSize_weight_(11.0, NSFontWeightBold), 11.0,
    )

    body_para = NSMutableParagraphStyle.alloc().init()
    body_para.setLineBreakMode_(NSLineBreakByWordWrapping)
    body_attrs = {
        NSFontAttributeName: body_font,
        NSParagraphStyleAttributeName: body_para,
    }
    chip_attrs = {NSFontAttributeName: chip_font}

    def measure_body(text: str) -> tuple[float, float]:
        if not text:
            return (0.0, 0.0)
        s = NSAttributedString.alloc().initWithString_attributes_(text, body_attrs)
        max_w = bubble_max_w - 2 * bubble_outer_pad
        max_h = bubble_max_h - 2 * bubble_outer_pad
        opts = NSStringDrawingUsesLineFragmentOrigin | NSStringDrawingUsesFontLeading
        rect = s.boundingRectWithSize_options_(NSMakeSize(max_w, max_h), opts)
        return (float(rect.size.width) + 1, min(float(rect.size.height), max_h))

    def measure_chip(text: str) -> tuple[float, float]:
        if not text:
            return (0.0, 0.0)
        s = NSAttributedString.alloc().initWithString_attributes_(text, chip_attrs)
        sz = s.size()
        return (float(sz.width) + 2 * chip_pad_x, float(sz.height) + 2 * chip_pad_y)

    def compute_bubble_size(name: str, label: str) -> tuple[float, float, float, float]:
        """Returns (bubble_w, bubble_h, chip_w, chip_h).

        Width is fixed at bubble_max_w so every bubble in the stack is
        uniform. Height grows with content up to bubble_max_h; the chip
        floats on the top border and doesn't consume interior height.
        """
        chip_w, chip_h = measure_chip(name)
        _body_w, body_h = measure_body(label)
        bw = bubble_max_w
        bh = min(body_h + 2 * bubble_outer_pad, bubble_max_h)
        return (bw, bh, chip_w, chip_h)

    class PetView(NSView):
        def initWithCfg_sprite_(self, cfg_in, sprite_in):
            self = objc.super(PetView, self).initWithFrame_(
                NSMakeRect(0, 0, sprite_w, sprite_h)
            )
            if self is None:
                return None
            self._cfg = cfg_in
            self._sprite = sprite_in
            self._frame_idx = 0
            self._state = "idle"
            self._still_row = 0
            self._still_col = 0
            self._ctrl = None
            opts = (
                NSTrackingMouseEnteredAndExited
                | NSTrackingActiveAlways
                | NSTrackingInVisibleRect
            )
            area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None,
            )
            self.addTrackingArea_(area)
            return self

        def mouseEntered_(self, _event):
            if self._ctrl is not None:
                self._ctrl.setHover_(True)

        def mouseExited_(self, _event):
            if self._ctrl is not None:
                self._ctrl.setHover_(False)

        def isFlipped(self):
            return False

        def drawRect_(self, dirty):
            NSColor.clearColor().set()
            NSRectFill(dirty)
            if self._state == "still":
                row = self._still_row
                col = self._still_col
            elif self._state == "drag_right":
                row, col = 1, self._frame_idx % 8
            elif self._state == "drag_left":
                row, col = 2, self._frame_idx % 8
            elif self._state == "hover":
                row, col = 7, self._frame_idx % 6
            else:
                anim = STATE_ANIMATION.get(self._state, STATE_ANIMATION["idle"])
                eff_idx = self._frame_idx // max(1, int(anim["div"]))
                row = max(0, min(int(anim["row"]), self._cfg["rows"] - 1))
                frames = max(1, int(anim["frames"]))
                hold = max(0, int(anim.get("holdFrames", 0)))
                cycle = eff_idx % (frames + hold)
                col = cycle if cycle < frames else 0
            sheet_h = self._cfg["rows"] * self._cfg["cellHeight"]
            src = NSRect(
                (col * self._cfg["cellWidth"], sheet_h - (row + 1) * self._cfg["cellHeight"]),
                (self._cfg["cellWidth"], self._cfg["cellHeight"]),
            )
            self._sprite.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                self.bounds(), src, NSCompositingOperationSourceOver, 1.0, False, None,
            )

        def advance_(self, _timer):
            self._frame_idx += 1
            if self._state != "still":
                self.setNeedsDisplay_(True)

        def setSpriteState_(self, state):
            if state != self._state:
                self._state = state
                self._frame_idx = 0
                self.setNeedsDisplay_(True)

    class BadgeView(NSView):
        """Filled circular badge with centered bold count text."""

        def initWithFrame_(self, frame):
            self = objc.super(BadgeView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._text = ""
            self._fill = theme_color
            return self

        def isFlipped(self):
            return False

        def drawRect_(self, _dirty):
            b = self.bounds()
            inset = 1.0
            ring = NSRect(
                (b.origin.x + inset, b.origin.y + inset),
                (b.size.width - 2 * inset, b.size.height - 2 * inset),
            )
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.20).set()
            NSBezierPath.bezierPathWithOvalInRect_(b).fill()
            self._fill.set()
            NSBezierPath.bezierPathWithOvalInRect_(ring).fill()
            if self._text:
                attrs = {
                    NSFontAttributeName: badge_font,
                    NSForegroundColorAttributeName: NSColor.whiteColor(),
                }
                s = NSAttributedString.alloc().initWithString_attributes_(self._text, attrs)
                sz = s.size()
                tx = b.origin.x + (b.size.width - sz.width) / 2.0
                ty = b.origin.y + (b.size.height - sz.height) / 2.0 - 0.5
                s.drawAtPoint_(NSMakePoint(tx, ty))

        def setText_(self, t):
            if t != self._text:
                self._text = t
                self.setNeedsDisplay_(True)

        def setFill_(self, c):
            self._fill = c
            self.setNeedsDisplay_(True)

    def make_chip() -> NSTextField:
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 1))
        f.setEditable_(False)
        f.setSelectable_(False)
        f.setBezeled_(False)
        f.setBordered_(False)
        f.setDrawsBackground_(True)
        f.setBackgroundColor_(theme_color)
        f.setFont_(chip_font)
        f.setTextColor_(NSColor.whiteColor())
        f.setAlignment_(NSTextAlignmentCenter)
        f.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        f.cell().setWraps_(False)
        f.cell().setUsesSingleLineMode_(True)
        f.setWantsLayer_(True)
        f.layer().setCornerRadius_(3.0)
        f.layer().setMasksToBounds_(True)
        f.setHidden_(True)
        return f

    def make_body() -> NSTextField:
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 1))
        f.setEditable_(False)
        f.setSelectable_(False)
        f.setBezeled_(False)
        f.setBordered_(False)
        f.setDrawsBackground_(False)
        f.setFont_(body_font)
        f.setTextColor_(NSColor.labelColor())
        f.setAlignment_(NSTextAlignmentLeft)
        f.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        f.cell().setWraps_(True)
        f.cell().setUsesSingleLineMode_(False)
        f.setHidden_(True)
        return f

    def make_bubble_effect() -> NSVisualEffectView:
        eff = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 1, 1))
        eff.setMaterial_(NSVisualEffectMaterialHUDWindow)
        eff.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        eff.setState_(NSVisualEffectStateActive)
        eff.setWantsLayer_(True)
        eff.layer().setCornerRadius_(bubble_corner)
        eff.layer().setMasksToBounds_(True)
        eff.setHidden_(True)
        return eff

    class DraggableContainer(NSView):
        def initWithFrame_controller_(self, frame, ctrl):
            self = objc.super(DraggableContainer, self).initWithFrame_(frame)
            if self is None:
                return None
            self._ctrl = ctrl
            self._drag_start_screen = None
            self._drag_start_origin = None
            self._drag_started = False
            self._click_target = None
            self._press_start_t = 0.0
            self._last_drag_x = 0.0
            self._right_click_target = None
            return self

        def isFlipped(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def _hit_widget(self, local):
            ctrl = self._ctrl
            if not ctrl.badgeView.isHidden() and NSPointInRect(local, ctrl.badgeView.frame()):
                return ("badge", -1)
            for i, (eff, _chip, _body) in enumerate(ctrl.bubble_pool):
                if not eff.isHidden() and NSPointInRect(local, eff.frame()):
                    return ("bubble", i)
            if NSPointInRect(local, ctrl.petView.frame()):
                return ("pet", -1)
            return None

        def hitTest_(self, point):
            if not NSPointInRect(point, self.frame()):
                return None
            local = NSMakePoint(point.x - self.frame().origin.x,
                                point.y - self.frame().origin.y)
            return self if self._hit_widget(local) is not None else None

        def rightMouseDown_(self, event):
            loc = event.locationInWindow()
            self._right_click_target = self._hit_widget(loc)

        def rightMouseUp_(self, _event):
            target = self._right_click_target
            self._right_click_target = None
            if target is None:
                return
            kind, idx = target
            ctrl = self._ctrl
            if kind == "pet":
                ctrl.cycleStillPose()
            elif kind == "bubble":
                ctrl.dismissBubble_(idx)

        def mouseDown_(self, event):
            loc = event.locationInWindow()
            self._click_target = self._hit_widget(loc)
            self._drag_start_screen = NSEvent.mouseLocation()
            f = self.window().frame()
            self._drag_start_origin = (float(f.origin.x), float(f.origin.y))
            self._drag_started = False
            self._press_start_t = time.monotonic()

        def mouseDragged_(self, _event):
            if self._drag_start_screen is None:
                return
            cur = NSEvent.mouseLocation()
            dx = cur.x - self._drag_start_screen.x
            dy = cur.y - self._drag_start_screen.y
            if not self._drag_started:
                if (dx * dx + dy * dy) < drag_threshold_px2:
                    return
                self._drag_started = True
                self._last_drag_x = cur.x
            new_x = self._drag_start_origin[0] + dx
            new_y = self._drag_start_origin[1] + dy
            self.window().setFrameOrigin_(NSMakePoint(new_x, new_y))
            # Frame-to-frame horizontal delta picks the running direction.
            # 1.5pt deadzone so vertical-only motion or jitter doesn't flip
            # her every poll.
            step = cur.x - self._last_drag_x
            if step >= 1.5:
                self._ctrl.setDragSprite_("drag_right")
                self._last_drag_x = cur.x
            elif step <= -1.5:
                self._ctrl.setDragSprite_("drag_left")
                self._last_drag_x = cur.x

        def mouseUp_(self, _event):
            ctrl = self._ctrl
            if self._drag_started:
                f = self.window().frame()
                pet_f = ctrl.petView.frame()
                ctrl._anchor_right = float(f.origin.x + pet_f.origin.x + pet_f.size.width)
                ctrl._anchor_bottom = float(f.origin.y + pet_f.origin.y)
                save_position(ctrl._anchor_right, ctrl._anchor_bottom)
                ctrl.endDrag()
            else:
                if self._click_target is not None:
                    kind, _idx = self._click_target
                    if kind == "bubble":
                        ctrl.collapseBubble()
                    elif kind == "badge":
                        ctrl.expandBubble()
                    elif kind == "pet":
                        ctrl.cycleManualState()
            self._drag_start_screen = None
            self._drag_started = False
            self._click_target = None

    class Controller(NSObject):
        def init(self):
            self = objc.super(Controller, self).init()
            if self is None:
                return None
            self._last_mtime = 0.0
            self._anchor_right = 0.0
            self._anchor_bottom = 0.0
            self._entries: list[dict] = []
            self._sizes: list[tuple[float, float, float, float]] = []
            self._count = 0
            self._badge_class = "working"
            self._collapsed = False
            self._derived_sprite = "idle"
            self._manual_sprite = None  # None = follow derived; else explicit state
            self._manual_still = None  # index into STILL_POSES when in 'still' mode
            self._drag_sprite = None  # transient override during a drag
            self._hover = False  # mouse currently over Nola
            self._dismissed_until: dict[str, float] = {}  # session_id → ts
            return self

        def pollState_(self, _timer):
            try:
                mtime = STATE_PATH.stat().st_mtime
            except OSError:
                mtime = 0.0
            raw_sessions = read_sessions()
            # Right-click on a bubble snoozes its session: the bubble is
            # hidden until that session's ts advances past the dismissal
            # timestamp (i.e., a fresh hook fires for it).
            sessions = {
                sid: s for sid, s in raw_sessions.items()
                if not (
                    isinstance(s, dict)
                    and float(s.get("ts", 0)) <= self._dismissed_until.get(sid, 0)
                )
            }
            sprite_state, entries, count, badge_class = derive_view(sessions, time.time())
            # Wake from manual override only on genuine attention-worthy
            # events: a fresh transition into `alert`, or a new active session
            # appearing from a 0-active baseline. Otherwise leave her
            # whichever manual state the user picked — ambient state churn
            # across multiple sessions should not reset her.
            prev_derived = self._derived_sprite
            prev_count = self._count
            new_alert = sprite_state == "alert" and prev_derived != "alert"
            new_activity = count > 0 and prev_count == 0
            if new_alert or new_activity:
                self._manual_sprite = None
                self._manual_still = None
            self._derived_sprite = sprite_state
            self.applyPetState()
            entries_changed = entries != self._entries
            count_changed = count != self._count
            class_changed = badge_class != self._badge_class
            self._entries = entries
            self._count = count
            self._badge_class = badge_class
            if entries_changed:
                self._sizes = [
                    compute_bubble_size(e["name"], e["label"]) for e in entries
                ]
            if count == 0:
                self._collapsed = False
            if mtime != self._last_mtime or class_changed or entries_changed or count_changed:
                self._last_mtime = mtime
                self.applyLayout()

        def applyPetState(self):
            # Priority: active drag > hover > user-picked manual > live derived.
            if self._drag_sprite:
                effective = self._drag_sprite
            elif self._hover:
                effective = "hover"
            else:
                effective = self._manual_sprite or self._derived_sprite
            self.petView.setSpriteState_(effective)

        def setHover_(self, flag):
            if bool(flag) != self._hover:
                self._hover = bool(flag)
                self.applyPetState()

        def setDragSprite_(self, state):
            if state != self._drag_sprite:
                self._drag_sprite = state
                self.applyPetState()

        def endDrag(self):
            if self._drag_sprite is not None:
                self._drag_sprite = None
                self.applyPetState()

        def dismissBubble_(self, index):
            if not (0 <= index < len(self._entries)):
                return
            sid = self._entries[index].get("session_id", "")
            if not sid:
                return
            s = read_sessions().get(sid, {})
            ts = float(s.get("ts", 0)) if isinstance(s, dict) else time.time()
            self._dismissed_until[sid] = ts
            self.pollState_(None)

        def clearManualState(self):
            self._manual_sprite = None
            self._manual_still = None
            self.applyPetState()

        def cycleManualState(self):
            try:
                idx = MANUAL_CYCLE.index(self._manual_sprite if self._manual_sprite != "still" else None)
            except ValueError:
                idx = -1
            self._manual_sprite = MANUAL_CYCLE[(idx + 1) % len(MANUAL_CYCLE)]
            self._manual_still = None
            self.applyPetState()

        def cycleStillPose(self):
            n = len(STILL_POSES)
            if self._manual_still is None:
                self._manual_still = 0
            else:
                self._manual_still = (self._manual_still + 1) % n
            pose = STILL_POSES[self._manual_still]
            if pose is None:
                self._manual_sprite = None
                self._manual_still = None
            else:
                _label, row, col = pose
                self.petView._still_row = row
                self.petView._still_col = col
                self._manual_sprite = "still"
            self.applyPetState()
            self.petView.setNeedsDisplay_(True)

        def collapseBubble(self):
            if self._count > 0 and not self._collapsed:
                self._collapsed = True
                self.applyLayout()

        def expandBubble(self):
            if self._collapsed:
                self._collapsed = False
                self.applyLayout()

        def applyLayout(self):
            self.petView.setHidden_(False)
            count = self._count
            n = len(self._entries)
            show_bubbles = count > 0 and not self._collapsed and n > 0
            show_badge = count > 0 and self._collapsed

            self.badgeView.setHidden_(not show_badge)
            for eff, chip, body in self.bubble_pool:
                eff.setHidden_(True)
                chip.setHidden_(True)
                body.setHidden_(True)

            # Determine which side of Nola the badge / bubbles sit on, based on
            # how much room exists between her anchor and the screen edges.
            screen = NSScreen.mainScreen() or NSScreen.screens()[0]
            vf = screen.visibleFrame()
            space_right = (vf.origin.x + vf.size.width) - self._anchor_right
            if show_bubbles:
                max_bw = max((s[0] for s in self._sizes), default=0.0)
                max_chip_h = max((s[3] for s in self._sizes), default=0.0)
                chip_overhang = max_chip_h / 2.0
                total_h = (
                    sum(s[1] for s in self._sizes)
                    + max(0, n - 1) * inter_bubble_gap
                    + chip_overhang  # room for the topmost bubble's chip
                )
                bubble_to_pet_gap = 4.0
                # Prefer right of Nola (matches default badge position). Flip
                # if right doesn't have room for the widest bubble.
                bubbles_on_right = space_right >= (max_bw + bubble_to_pet_gap + 8)
                win_w = sprite_w + bubble_to_pet_gap + max_bw
                win_h = max(sprite_h, total_h)
                if bubbles_on_right:
                    sprite_x = 0.0
                else:
                    sprite_x = max_bw + bubble_to_pet_gap
                sprite_y = 0.0
            elif show_badge:
                bubbles_on_right = space_right >= (badge_protrude + 4)
                win_w = sprite_w + badge_protrude
                win_h = sprite_h + badge_protrude
                sprite_y = badge_protrude
                sprite_x = 0.0 if bubbles_on_right else badge_protrude
            else:
                bubbles_on_right = True
                win_w = sprite_w
                win_h = sprite_h
                sprite_x = 0.0
                sprite_y = 0.0

            self.petView.setFrame_(NSMakeRect(sprite_x, sprite_y, sprite_w, sprite_h))

            if show_bubbles:
                # Bubbles sit at Nola's bottom level (same y as the badge),
                # stacking upward. Newest session is at the bottom — directly
                # adjacent to where the badge would have been.
                if bubbles_on_right:
                    bubble_anchor_x = sprite_x + sprite_w + bubble_to_pet_gap
                else:
                    bubble_anchor_x = sprite_x - bubble_to_pet_gap  # right edge target
                y_cursor = 0.0
                for i in range(min(n, len(self.bubble_pool))):
                    eff, chip, body = self.bubble_pool[i]
                    bw, bh, chip_w, chip_h = self._sizes[i]
                    if bubbles_on_right:
                        bx = bubble_anchor_x
                    else:
                        bx = bubble_anchor_x - bw
                    by = y_cursor
                    eff.setFrame_(NSMakeRect(bx, by, bw, bh))
                    eff.setHidden_(False)
                    name = self._entries[i].get("name") or ""
                    label = self._entries[i].get("label") or ""
                    body_x = bx + bubble_outer_pad
                    body_w = bw - 2 * bubble_outer_pad
                    body_y = by + bubble_outer_pad
                    body_h = bh - 2 * bubble_outer_pad
                    body.setStringValue_(label)
                    body.setFrame_(NSMakeRect(body_x, body_y, body_w, body_h))
                    body.setHidden_(False)
                    # Chip floats on the top border, centered on the edge.
                    # When bubbles sit on Nola's right, the chip hugs the
                    # bubble's right edge; when on the left, the left edge.
                    if name and chip_h > 0:
                        chip.setStringValue_(name)
                        if bubbles_on_right:
                            chip_x = bx + bw - chip_w - chip_inset
                        else:
                            chip_x = bx + chip_inset
                        chip_y = by + bh - chip_h / 2.0
                        chip.setFrame_(NSMakeRect(chip_x, chip_y, chip_w, chip_h))
                        chip.setHidden_(False)
                    else:
                        chip.setHidden_(True)
                    y_cursor += bh + inter_bubble_gap
            elif show_badge:
                self.badgeView.setText_(str(count) if count <= 99 else "99+")
                self.badgeView.setFill_(
                    done_color if self._badge_class == "attention" else theme_color
                )
                if bubbles_on_right:
                    bx = sprite_x + sprite_w - badge_d * badge_overlap
                else:
                    bx = sprite_x - badge_d * (1.0 - badge_overlap)
                by = sprite_y - badge_d * (1.0 - badge_overlap)
                self.badgeView.setFrame_(NSMakeRect(bx, by, badge_d, badge_d))
                self.badgeView.setNeedsDisplay_(True)

            win_x = self._anchor_right - (sprite_x + sprite_w)
            win_y = self._anchor_bottom - sprite_y
            self.container.setFrame_(NSMakeRect(0, 0, win_w, win_h))
            self.window.setFrame_display_(NSMakeRect(win_x, win_y, win_w, win_h), True)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    saved = load_position()
    screen = NSScreen.mainScreen() or NSScreen.screens()[0]
    vf = screen.visibleFrame()
    m = float(cfg["margin"])
    if saved is not None:
        anchor_right, anchor_bottom = saved
    else:
        anchor = cfg.get("anchor", "bottom-right")
        if anchor == "bottom-left":
            anchor_right = vf.origin.x + sprite_w + m
            anchor_bottom = vf.origin.y + m
        elif anchor == "top-right":
            anchor_right = vf.origin.x + vf.size.width - m
            anchor_bottom = vf.origin.y + vf.size.height - sprite_h - m
        elif anchor == "top-left":
            anchor_right = vf.origin.x + sprite_w + m
            anchor_bottom = vf.origin.y + vf.size.height - sprite_h - m
        else:
            anchor_right = vf.origin.x + vf.size.width - m
            anchor_bottom = vf.origin.y + m

    win_rect = NSMakeRect(anchor_right - sprite_w, anchor_bottom, sprite_w, sprite_h)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        win_rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False,
    )
    window.setOpaque_(False)
    window.setBackgroundColor_(NSColor.clearColor())
    window.setHasShadow_(False)
    window.setLevel_(NSStatusWindowLevel)
    window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorIgnoresCycle
    )
    window.setIgnoresMouseEvents_(False)
    window.setMovableByWindowBackground_(False)

    controller = Controller.alloc().init()
    controller._anchor_right = anchor_right
    controller._anchor_bottom = anchor_bottom

    container = DraggableContainer.alloc().initWithFrame_controller_(
        NSMakeRect(0, 0, sprite_w, sprite_h), controller,
    )
    window.setContentView_(container)

    pet_view = PetView.alloc().initWithCfg_sprite_(cfg, sprite)
    pet_view.setFrame_(NSMakeRect(0, 0, sprite_w, sprite_h))
    pet_view._ctrl = controller
    container.addSubview_(pet_view)

    bubble_pool: list[tuple] = []
    for _ in range(MAX_BUBBLES + 1):
        eff = make_bubble_effect()
        container.addSubview_(eff)
        chip = make_chip()
        container.addSubview_(chip)
        body = make_body()
        container.addSubview_(body)
        bubble_pool.append((eff, chip, body))

    badge_view = BadgeView.alloc().initWithFrame_(NSMakeRect(0, 0, badge_d, badge_d))
    badge_view.setHidden_(True)
    container.addSubview_(badge_view)

    controller.window = window
    controller.container = container
    controller.petView = pet_view
    controller.bubble_pool = bubble_pool
    controller.badgeView = badge_view

    sessions = read_sessions()
    sprite_state, entries, count, badge_class = derive_view(sessions, time.time())
    controller._entries = entries
    controller._sizes = [compute_bubble_size(e["name"], e["label"]) for e in entries]
    controller._count = count
    controller._badge_class = badge_class
    controller._derived_sprite = sprite_state
    controller.applyPetState()
    controller.applyLayout()
    window.orderFrontRegardless()

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        1.0 / float(cfg["fps"]), True, pet_view.advance_,
    )
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        0.25, True, controller.pollState_,
    )

    app.run()


def dump_previews(out_dir: str) -> None:
    from AppKit import (
        NSBitmapImageFileTypePNG, NSBitmapImageRep, NSColor,
        NSCompositingOperationSourceOver, NSDeviceRGBColorSpace,
        NSGraphicsContext, NSImage, NSRect, NSRectFill,
    )

    cfg = load_config()
    sprite_path = cfg["spritesheet"]
    sprite = NSImage.alloc().initWithContentsOfFile_(sprite_path)
    if sprite is None:
        sys.stderr.write(f"pet: failed to load spritesheet at {sprite_path}\n")
        sys.exit(2)

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    strip_w = cfg["cols"] * cfg["cellWidth"]
    strip_h = cfg["cellHeight"]
    sheet_h = cfg["rows"] * cfg["cellHeight"]

    for row in range(cfg["rows"]):
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(
            None, strip_w, strip_h, 8, 4, True, False,
            NSDeviceRGBColorSpace, 0, 0, 0,
        )
        ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(ctx)
        NSColor.clearColor().set()
        NSRectFill(NSRect((0, 0), (strip_w, strip_h)))
        src_y = sheet_h - (row + 1) * cfg["cellHeight"]
        src = NSRect((0, src_y), (strip_w, strip_h))
        sprite.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            NSRect((0, 0), (strip_w, strip_h)), src,
            NSCompositingOperationSourceOver, 1.0, False, None,
        )
        NSGraphicsContext.restoreGraphicsState()
        png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        path = out / f"row-{row:02d}.png"
        png.writeToFile_atomically_(str(path), True)
        print(f"wrote {path}")


def print_current() -> None:
    sessions = read_sessions()
    sprite_state, entries, count, badge_class = derive_view(sessions, time.time())
    print(f"sprite:       {sprite_state}")
    print(f"badge:        {badge_class}  ({'green' if badge_class == 'attention' else 'theme'})")
    print(f"active count: {count}")
    for e in entries:
        name = e.get("name") or "·"
        print(f"  [{name}] {e.get('label')}")


def main() -> None:
    args = sys.argv[1:]
    sub = args[0] if args else "run"
    if sub == "run":
        run_overlay()
    elif sub == "previews":
        dump_previews(args[1] if len(args) >= 2 else str(PET_HOME / "previews"))
    elif sub == "set-state":
        if len(args) < 2:
            sys.stderr.write("pet: set-state needs a state name\n")
            sys.exit(64)
        write_manual_state(args[1])
        print(f"state={args[1]} (session=manual)")
    elif sub == "current":
        print_current()
    else:
        sys.stderr.write("usage: overlay.py [run|previews [out-dir]|set-state <name>|current]\n")
        sys.exit(64)


if __name__ == "__main__":
    main()
