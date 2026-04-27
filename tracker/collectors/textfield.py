"""
tracker/collectors/textfield.py

Reads the current value of the focused text field on macOS
using the Accessibility API (pyobjc).

Works for:
- Chrome / Chromium (web text inputs including ChatGPT, Gemini, Gmail)
- Claude Desktop, ChatGPT Desktop (Electron - enabled accessibility)
- Obsidian, VS Code (Electron)
- Native macOS apps

Does NOT work for:
- Apps that explicitly disable accessibility (rare)
- Screen-locked state (caught upstream by daemon)

This module is macOS-only. On other platforms, collect() always returns None.
The import of pyobjc happens inside collect() so the module can be imported
safely on non-macOS systems (e.g. CI, Linux dev machines).
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IS_MACOS = platform.system() == "Darwin"

# Apps whose AXTextArea exposes an entire scrollback buffer (Terminal shows
# the full shell history, thousands of chars including commands/secrets).
# We never sample these — app_name + window_title remain available.
_EXCLUDED_APPS: frozenset[str] = frozenset({
    "Terminal",
    "iTerm",
    "iTerm2",
})

# Unicode control chars (left-to-right mark etc.) that macOS prepends to
# some localised app names (e.g. '\u200eWhatsApp').
_UNICODE_CONTROL_PREFIXES = ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c")


def _strip_control_prefix(name: str) -> str:
    while name and name[0] in _UNICODE_CONTROL_PREFIXES:
        name = name[1:]
    return name


def _is_excluded_app(app_name: str) -> bool:
    return _strip_control_prefix(app_name) in _EXCLUDED_APPS


@dataclass
class TextFieldSample:
    """A sample of text from the currently focused field."""
    app_name: str
    sample: str           # last N chars of field value
    full_length: int      # total length of field content
    field_role: str       # AXTextField, AXTextArea, AXComboBox, etc.


class TextFieldSampler:
    """
    Reads text field content using the macOS Accessibility API.

    Sampling strategy:
    - Read full AXValue of focused element
    - Return last `sample_chars` characters (most recently typed content)
    - Track previous sample to detect changes

    Never raises — all errors return None with a debug log.
    """

    def __init__(self, sample_chars: int = 300) -> None:
        self._sample_chars = sample_chars
        self._last_sample: str | None = None
        self._permission_warned = False

    def collect(self) -> TextFieldSample | None:
        """
        Read the focused text field.
        Returns None if:
        - Not on macOS
        - pyobjc not installed
        - No focused text field
        - Accessibility permission not granted
        - Any other error
        """
        if not _IS_MACOS:
            return None

        try:
            return self._read_focused_field()
        except PermissionError as exc:
            if not self._permission_warned:
                logger.warning(
                    "Accessibility permission not granted. "
                    "Grant in System Settings → Privacy → Accessibility → Terminal. "
                    "Error: %s",
                    exc,
                )
                self._permission_warned = True
            return None
        except ImportError:
            logger.debug("pyobjc not installed — text field sampling disabled")
            return None
        except RuntimeError as exc:
            logger.debug("Text field read runtime error: %s", exc)
            return None
        except OSError as exc:
            logger.debug("Text field read OS error: %s", exc)
            return None

    def has_changed(self, sample: TextFieldSample) -> bool:
        """Return True if the field content changed since last call."""
        changed = sample.sample != self._last_sample
        self._last_sample = sample.sample
        return changed

    def _read_focused_field(self) -> TextFieldSample | None:
        """
        Core Accessibility API read.
        Raises ImportError if pyobjc unavailable.
        Raises PermissionError if accessibility not granted.
        Raises RuntimeError on unexpected AX errors.
        """
        # Import here so non-macOS environments can import this module
        try:
            from ApplicationServices import (  # type: ignore[import-untyped]
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
                kAXErrorSuccess,
                kAXFocusedUIElementAttribute,
                kAXRoleAttribute,
                kAXValueAttribute,
            )
        except ImportError:
            raise ImportError("ApplicationServices framework not available via pyobjc")

        frontmost_name, frontmost_pid = _frontmost_app_via_quartz()
        if frontmost_pid is None:
            return None

        if _is_excluded_app(frontmost_name or ""):
            return None

        # Query AX tree for the frontmost app by PID. This avoids the
        # system-wide focused element leaking text from a background app
        # (e.g. Chrome focus bleeding into WhatsApp snapshots).
        ax_app = AXUIElementCreateApplication(frontmost_pid)

        error, focused = AXUIElementCopyAttributeValue(
            ax_app,
            kAXFocusedUIElementAttribute,
            None,
        )

        if error != kAXErrorSuccess or focused is None:
            return None

        # Get the role — we only want text fields
        error, role = AXUIElementCopyAttributeValue(
            focused,
            kAXRoleAttribute,
            None,
        )
        if error != kAXErrorSuccess or role is None:
            return None

        role_str = str(role)
        if role_str not in ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"):
            return None

        # Get the value
        error, value = AXUIElementCopyAttributeValue(
            focused,
            kAXValueAttribute,
            None,
        )

        if error != kAXErrorSuccess or value is None:
            return None

        full_text = str(value)
        full_length = len(full_text)

        # Take last N chars — most recently typed = most relevant
        sample = full_text[-self._sample_chars:] if full_length > self._sample_chars else full_text

        return TextFieldSample(
            app_name=_strip_control_prefix(frontmost_name or "unknown"),
            sample=sample,
            full_length=full_length,
            field_role=role_str,
        )


def _frontmost_app_via_quartz() -> tuple[str | None, int | None]:
    """Return (app_name, pid) of the topmost on-screen regular window.
    Uses Quartz CGWindowList which queries the window server directly —
    no run-loop / notification dependency (unlike NSWorkspace)."""
    try:
        from Quartz import (  # type: ignore[import-untyped]
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError:
        return None, None

    opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID)
    if not windows:
        return None, None

    for w in windows:
        if w.get("kCGWindowLayer", 99) != 0:
            continue
        name = w.get("kCGWindowOwnerName")
        pid = w.get("kCGWindowOwnerPID")
        if name and pid and name not in ("WindowManager", "Dock", "Window Server"):
            return str(name), int(pid)
    return None, None


