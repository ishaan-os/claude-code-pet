#!/usr/bin/env python3
"""Detect the active terminal's theme + typography and apply to pet config.

Run from the user's terminal (so $TERM_PROGRAM / $ITERM_PROFILE propagate).
Updates ~/.claude/pet/config.json in place. Prints a human-readable summary.

Supported terminals:
- iTerm.app (full: active profile colors + font)
- Apple Terminal (via osascript; colors only, font name)
- Ghostty (parses the user's config file)

For any other terminal, falls back to system accent + system monospaced font
(equivalent to themeAccent="system", bubbleFontFamily unset).
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".claude" / "pet" / "config.json"

# Fields written into config.json. Anything else is left untouched.
THEME_KEYS = (
    "themeAccent",
    "doneAccent",
    "bubbleFontFamily",
    "chipFontFamily",
    "bubbleFontSize",
)


def _to_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _parse_font_string(s: str) -> tuple[Optional[str], Optional[float]]:
    """Parse an iTerm-style font string: 'PostScriptName <size>' or just name."""
    if not s:
        return (None, None)
    m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)\s*$", s.strip())
    if m:
        return (m.group(1).strip() or None, float(m.group(2)))
    return (s.strip() or None, None)


def _iterm_profile_dict(profile_name: str) -> Optional[dict]:
    plist = Path.home() / "Library" / "Preferences" / "com.googlecode.iterm2.plist"
    if not plist.exists():
        return None
    try:
        with open(plist, "rb") as f:
            data = plistlib.load(f)
    except Exception:
        return None
    profiles = data.get("New Bookmarks") or []
    for p in profiles:
        if p.get("Name") == profile_name:
            return p
    if profiles:
        return profiles[0]
    return None


def _iterm_color(profile: dict, key: str) -> Optional[tuple[float, float, float]]:
    c = profile.get(key)
    if not isinstance(c, dict):
        return None
    try:
        return (
            float(c.get("Red Component", 0)),
            float(c.get("Green Component", 0)),
            float(c.get("Blue Component", 0)),
        )
    except (TypeError, ValueError):
        return None


def detect_iterm() -> Optional[dict]:
    profile_name = os.environ.get("ITERM_PROFILE", "Default")
    profile = _iterm_profile_dict(profile_name)
    if profile is None:
        return None

    # Use ANSI cyan (6) for the working accent — closest analog to iTerm's
    # natural "in-progress" cue. ANSI green (2) for done/alert.
    accent_rgb = (
        _iterm_color(profile, "Ansi 6 Color")
        or _iterm_color(profile, "Ansi 14 Color")
        or _iterm_color(profile, "Foreground Color")
    )
    done_rgb = (
        _iterm_color(profile, "Ansi 2 Color")
        or _iterm_color(profile, "Ansi 10 Color")
    )

    font_family, font_size = _parse_font_string(profile.get("Normal Font", ""))

    out: dict = {"_terminal": f"iTerm.app (profile: {profile_name})"}
    if accent_rgb:
        out["themeAccent"] = _to_hex(*accent_rgb)
    if done_rgb:
        out["doneAccent"] = _to_hex(*done_rgb)
    if font_family:
        out["bubbleFontFamily"] = font_family
        out["chipFontFamily"] = font_family
    if font_size:
        out["bubbleFontSize"] = max(11.0, min(16.0, font_size * 0.85))
    return out


def detect_apple_terminal() -> Optional[dict]:
    """Pull colors via AppleScript; font via the active settings set."""
    out: dict = {"_terminal": "Apple Terminal"}

    def _rgb_from_osascript(prop: str) -> Optional[tuple[float, float, float]]:
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 f'tell application "Terminal" to get {prop} of selected tab of window 1'],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            if len(parts) != 3:
                return None
            vals = [float(p) / 65535.0 for p in parts]
            return (vals[0], vals[1], vals[2])
        except Exception:
            return None

    bg = _rgb_from_osascript("background color")
    fg = _rgb_from_osascript("normal text color")
    if bg or fg:
        # No direct "accent" — fall back to foreground for theme color.
        if fg:
            out["themeAccent"] = _to_hex(*fg)
        out["doneAccent"] = "system-green"

    # Font from the active settings set.
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Terminal" to get font name of selected tab of window 1'],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            out["bubbleFontFamily"] = r.stdout.strip()
            out["chipFontFamily"] = r.stdout.strip()
    except Exception:
        pass

    return out if len(out) > 1 else None


def _ghostty_config_path() -> Optional[Path]:
    for p in (
        Path.home() / ".config" / "ghostty" / "config",
        Path.home() / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config",
    ):
        if p.exists():
            return p
    return None


def detect_ghostty() -> Optional[dict]:
    path = _ghostty_config_path()
    if path is None:
        return None
    out: dict = {"_terminal": "Ghostty"}
    try:
        text = path.read_text()
    except OSError:
        return None
    keys: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip().strip('"')
    # Pull a usable accent: foreground or palette[6] (ANSI cyan). Ghostty
    # config doesn't itself expose a single "accent" knob.
    accent = keys.get("foreground") or keys.get("palette") or ""
    if accent.startswith("#") and len(accent) >= 7:
        out["themeAccent"] = accent[:7].upper()
    if (g := keys.get("palette", "")).startswith("2=#"):
        out["doneAccent"] = g.split("=", 1)[1][:7].upper()
    font_family = keys.get("font-family")
    if font_family:
        out["bubbleFontFamily"] = font_family
        out["chipFontFamily"] = font_family
    size = keys.get("font-size")
    if size:
        try:
            out["bubbleFontSize"] = max(11.0, min(16.0, float(size) * 0.85))
        except ValueError:
            pass
    return out if len(out) > 1 else None


def detect_system_fallback() -> dict:
    return {
        "_terminal": f"{os.environ.get('TERM_PROGRAM', 'unknown')} (fallback)",
        "themeAccent": "system",
        "doneAccent": "system-green",
    }


def detect() -> dict:
    term = os.environ.get("TERM_PROGRAM", "").strip()
    if term == "iTerm.app":
        return detect_iterm() or detect_system_fallback()
    if term == "Apple_Terminal":
        return detect_apple_terminal() or detect_system_fallback()
    if term == "ghostty":
        return detect_ghostty() or detect_system_fallback()
    return detect_system_fallback()


def apply(detected: dict) -> dict:
    """Merge detected theme keys into ~/.claude/pet/config.json. Returns the
    diff that was actually applied."""
    if not CONFIG_PATH.exists():
        sys.stderr.write(
            f"theme_detect: {CONFIG_PATH} does not exist. Run /pet start first.\n"
        )
        sys.exit(1)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        sys.stderr.write(f"theme_detect: failed to parse config: {e}\n")
        sys.exit(1)

    diff = {}
    for k in THEME_KEYS:
        if k in detected:
            old = cfg.get(k)
            new = detected[k]
            if old != new:
                diff[k] = {"old": old, "new": new}
            cfg[k] = new

    backup = CONFIG_PATH.with_suffix(".json.bak")
    try:
        shutil.copy2(CONFIG_PATH, backup)
    except Exception:
        backup = None  # type: ignore[assignment]

    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, CONFIG_PATH)
    return diff


def main() -> None:
    detected = detect()
    print(f"detected: {detected.get('_terminal')}")
    print("values:")
    for k in THEME_KEYS:
        v = detected.get(k, "(unchanged)")
        print(f"  {k:20s} = {v}")

    diff = apply(detected)
    if not diff:
        print("\nconfig already matches; nothing changed.")
        return

    print("\nupdated ~/.claude/pet/config.json:")
    for k, change in diff.items():
        print(f"  {k}: {change['old']} → {change['new']}")
    print("\nrun /pet restart to apply.")


if __name__ == "__main__":
    main()
