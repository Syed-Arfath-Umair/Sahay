"""Small Win32 helpers used by the UI."""
from __future__ import annotations

import platform

IS_WIN = platform.system() == "Windows"


def exclude_from_capture(widget) -> None:
    """Hide our overlay windows from screenshots (Win10 2004+), so Sahay never sees itself."""
    if not IS_WIN:
        return
    try:
        import ctypes

        WDA_EXCLUDEFROMCAPTURE = 0x11
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def force_foreground(widget) -> None:
    """Windows blocks background apps from taking focus; attach to the foreground thread to allow it."""
    widget.raise_()
    widget.activateWindow()
    if not IS_WIN:
        return
    try:
        import ctypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = int(widget.winId())
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != me:
            user32.AttachThreadInput(fg_thread, me, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(fg_thread, me, False)
        else:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def dark_title_bar(widget) -> None:
    if not IS_WIN:
        return
    try:
        import ctypes

        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # Windows 11 rounded corners + Mica backdrop where supported
        corner = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), 33, ctypes.byref(corner),
                                                   ctypes.sizeof(corner))
    except Exception:
        pass


def phys_to_logical(x: int, y: int):
    """Screenshot/OCR coordinates are physical pixels; Qt works in logical (DPI-scaled) pixels."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QGuiApplication

    for s in QGuiApplication.screens():
        g = s.geometry()
        r = s.devicePixelRatio()
        native = QRectF(g.x(), g.y(), g.width() * r, g.height() * r)
        if native.contains(x, y):
            return QPointF(g.x() + (x - g.x()) / r, g.y() + (y - g.y()) / r)
    r = QGuiApplication.primaryScreen().devicePixelRatio()
    return QPointF(x / r, y / r)
