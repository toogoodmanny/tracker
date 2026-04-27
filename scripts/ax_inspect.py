"""
scripts/ax_inspect.py

Diagnostic: prints the Accessibility attributes of the focused text element
in the frontmost app, every 2 seconds. Uses Quartz CGWindowList to detect
frontmost (no notification caching) and AXUIElementCreateApplication to
query the frontmost app's AX tree directly.

Usage:
    cd ~/tracker && source .venv/bin/activate
    python scripts/ax_inspect.py
"""

from __future__ import annotations

import time
from typing import Any

from ApplicationServices import (  # type: ignore[import-untyped]
    AXUIElementCopyAttributeNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    kAXErrorSuccess,
    kAXFocusedUIElementAttribute,
    kAXRoleAttribute,
    kAXSubroleAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
)
from Quartz import (  # type: ignore[import-untyped]
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
)


def _get_attr(element: Any, attr: str) -> Any:
    err, val = AXUIElementCopyAttributeValue(element, attr, None)
    if err != kAXErrorSuccess:
        return None
    return val


def _frontmost_app() -> tuple[str | None, int | None]:
    """Return (app_name, pid) of the topmost on-screen window owner."""
    opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID)
    if not windows:
        return None, None
    for w in windows:
        layer = w.get("kCGWindowLayer", 99)
        name = w.get("kCGWindowOwnerName")
        pid = w.get("kCGWindowOwnerPID")
        if layer == 0 and name and name not in ("WindowManager", "Dock", "Window Server"):
            return name, pid
    return None, None


def _truncate(text: str, n: int = 100) -> str:
    text = text.replace("\n", " ").replace("\r", " ")
    return text if len(text) <= n else text[:n] + f"... (+{len(text) - n})"


def inspect_once() -> None:
    app_name, pid = _frontmost_app()
    ts = time.strftime("%H:%M:%S")

    if pid is None:
        print(f"[{ts}] no frontmost app detected")
        return

    # Query AX tree of the frontmost app directly (by PID)
    ax_app = AXUIElementCreateApplication(pid)

    err, focused = AXUIElementCopyAttributeValue(
        ax_app, kAXFocusedUIElementAttribute, None
    )

    if err != kAXErrorSuccess or focused is None:
        # Also try system-wide as a fallback
        sys_wide = AXUIElementCreateSystemWide()
        err2, focused_sys = AXUIElementCopyAttributeValue(
            sys_wide, kAXFocusedUIElementAttribute, None
        )
        if err2 != kAXErrorSuccess or focused_sys is None:
            print(f"[{ts}] frontmost={app_name!r} pid={pid} | no focused element (app_err={err}, sys_err={err2})")
            return
        focused = focused_sys
        print(f"[{ts}] frontmost={app_name!r} pid={pid} | focused via system-wide (not app-scoped)")

    role = _get_attr(focused, kAXRoleAttribute)
    subrole = _get_attr(focused, kAXSubroleAttribute)
    value = _get_attr(focused, kAXValueAttribute)
    title = _get_attr(focused, kAXTitleAttribute)

    err3, attr_names = AXUIElementCopyAttributeNames(focused, None)
    attrs = list(attr_names) if err3 == kAXErrorSuccess and attr_names else []

    dom_role = _get_attr(focused, "AXDOMRole") if "AXDOMRole" in attrs else None
    dom_class = _get_attr(focused, "AXDOMClassList") if "AXDOMClassList" in attrs else None
    description = _get_attr(focused, "AXDescription") if "AXDescription" in attrs else None
    placeholder = _get_attr(focused, "AXPlaceholderValue") if "AXPlaceholderValue" in attrs else None

    value_str = "" if value is None else _truncate(str(value))

    print(f"[{ts}] frontmost={app_name!r} pid={pid}")
    print(f"    role={role!r} subrole={subrole!r} DOMRole={dom_role!r}")
    print(f"    title={title!r} desc={description!r} placeholder={placeholder!r}")
    print(f"    DOMClass={dom_class}")
    print(f"    value={value_str!r}")
    print(f"    attrs={attrs}")


def main() -> None:
    print("AX inspect: frontmost detection via Quartz, focused via per-app AX.")
    print("Click into each app's text field (WhatsApp, Telegram, Word, Obsidian, Chrome, Claude, ChatGPT).")
    print("Type a character in each so the field is focused. Ctrl-C here when done.")
    print("-" * 80)
    try:
        while True:
            inspect_once()
            print()
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
