"""Screen context: screenshot, active window title, cursor position (Windows)."""
from __future__ import annotations

import io
import platform
from dataclasses import dataclass

from PIL import Image


@dataclass
class ScreenShot:
    png: bytes            # downscaled PNG sent to models
    image: Image.Image    # full-resolution image (for OCR)
    left: int             # monitor origin in virtual-desktop coords
    top: int
    width: int            # full-res size
    height: int
    scale: float          # full-res px per downscaled px


def active_window_title() -> str:
    if platform.system() != "Windows":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def foreground_hwnd() -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def cursor_pos() -> tuple[int, int]:
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        p = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    except Exception:
        return 0, 0


def enable_dpi_awareness() -> None:
    """So screenshot pixels == real screen pixels on 4K/HiDPI HP panels."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def capture(max_side: int = 1280) -> ScreenShot:
    """Capture the monitor the mouse cursor is on."""
    import mss

    cx, cy = cursor_pos()
    with mss.mss() as sct:
        mon = sct.monitors[1]
        for m in sct.monitors[1:]:
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                mon = m
                break
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    scale = max(img.width, img.height) / max_side if max(img.width, img.height) > max_side else 1.0
    small = img.resize((round(img.width / scale), round(img.height / scale)), Image.LANCZOS) if scale > 1 else img
    buf = io.BytesIO()
    small.save(buf, format="PNG", optimize=True)
    return ScreenShot(buf.getvalue(), img, mon["left"], mon["top"], img.width, img.height, scale)
