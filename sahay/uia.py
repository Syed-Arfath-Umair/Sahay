"""Windows UI Automation: the real, clickable elements on screen (buttons, menus, tabs,
search boxes, taskbar icons…) with their exact positions. Runs on-device.

Giving the model a numbered list of real elements and letting it pick one is far more
accurate than asking a model to guess pixel coordinates from a screenshot.
"""
from __future__ import annotations

import time

from .ocr import OcrLine

CLICKABLE = {
    "ButtonControl", "SplitButtonControl", "MenuItemControl", "TabItemControl", "ListItemControl",
    "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "EditControl",
    "TreeItemControl", "MenuBarControl", "DataItemControl",
}
SHORT = {"ButtonControl": "Button", "SplitButtonControl": "Button", "MenuItemControl": "Menu",
         "TabItemControl": "Tab", "ListItemControl": "Item", "HyperlinkControl": "Link",
         "CheckBoxControl": "Checkbox", "RadioButtonControl": "Option", "ComboBoxControl": "Dropdown",
         "EditControl": "Textbox", "TreeItemControl": "Item", "MenuBarControl": "Menubar",
         "DataItemControl": "Item"}


def available() -> bool:
    try:
        import uiautomation  # noqa: F401

        return True
    except Exception:
        return False


def _taskbar_handles() -> list[int]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hs = [user32.FindWindowW("Shell_TrayWnd", None)]
        h = 0
        while True:  # secondary-monitor taskbars
            h = user32.FindWindowExW(None, h, "Shell_SecondaryTrayWnd", None)
            if not h:
                break
            hs.append(h)
        return [x for x in hs if x]
    except Exception:
        return []


def collect(foreground_hwnd: int, shot, start_id: int = 1, max_items: int = 140,
            budget_s: float = 1.2) -> list[OcrLine]:
    """Return clickable elements of the foreground window + taskbar, as OcrLine-like targets
    with coordinates relative to the captured monitor (same space as OCR)."""
    if shot is None or not available():
        return []
    import uiautomation as auto

    out: list[OcrLine] = []
    seen: set = set()
    deadline = time.monotonic() + budget_s
    mx0, my0, mx1, my1 = shot.left, shot.top, shot.left + shot.width, shot.top + shot.height

    with auto.UIAutomationInitializerInThread():
        roots = []
        if foreground_hwnd:
            roots.append((foreground_hwnd, max_items - 40))
        roots += [(h, 40) for h in _taskbar_handles()]   # Start, Search, pinned apps - always included
        for hwnd, quota in roots:
            try:
                root = auto.ControlFromHandle(hwnd)
            except Exception:
                continue
            if root is None:
                continue
            count = 0
            try:
                for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=25):
                    if time.monotonic() > deadline or count >= quota:
                        break
                    try:
                        ct = ctrl.ControlTypeName
                        if ct not in CLICKABLE:
                            continue
                        name = (ctrl.Name or "").strip()
                        if not name or ctrl.IsOffscreen:
                            continue
                        r = ctrl.BoundingRectangle
                        w, h = r.right - r.left, r.bottom - r.top
                        if w < 4 or h < 4 or w > shot.width * 0.9:
                            continue
                        cx, cy = r.left + w // 2, r.top + h // 2
                        if not (mx0 <= cx < mx1 and my0 <= cy < my1):
                            continue
                        key = (name, cx // 6, cy // 6)
                        if key in seen:
                            continue
                        seen.add(key)
                        label = f"[{SHORT.get(ct, 'Item')}] {name[:60]}"
                        out.append(OcrLine(start_id + len(out), label, r.left - mx0, r.top - my0, w, h))
                        count += 1
                    except Exception:
                        continue
            except Exception:
                continue
    return out
