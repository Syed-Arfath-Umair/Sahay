"""Native Win32 global hotkey + Esc listener (no third-party library; works on ARM64 and x64).

* The shortcut uses RegisterHotKey (the official Windows API for global hotkeys).
* Esc uses a low-level keyboard hook that only *observes* keys - it never blocks Esc for other apps.
Both run on one background thread with its own Windows message loop.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

MOD = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8, "cmd": 0x8}
MOD_NOREPEAT = 0x4000
VK_NAMES = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
            **{f"f{i}": 0x6F + i for i in range(1, 13)}}
WM_HOTKEY, WM_KEYDOWN, WM_SYSKEYDOWN, WM_QUIT = 0x0312, 0x0100, 0x0104, 0x0012
WH_KEYBOARD_LL, VK_ESCAPE = 13, 0x1B


def parse(hotkey: str) -> tuple[int, int]:
    """'<ctrl>+<alt>+<space>' -> (modifiers, virtual key)."""
    mods, vk = 0, 0
    for part in hotkey.lower().split("+"):
        k = part.strip().strip("<>")
        if k in MOD:
            mods |= MOD[k]
        elif k in VK_NAMES:
            vk = VK_NAMES[k]
        elif len(k) == 1:
            vk = ord(k.upper())
    if not vk:
        raise ValueError(f"Unsupported hotkey: {hotkey}")
    return mods, vk


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class NativeHotkeys:
    def __init__(self, hotkey: str, on_hotkey, on_escape):
        self.hotkey, self.on_hotkey, self.on_escape = hotkey, on_hotkey, on_escape
        self._thread_id = 0
        self._ready = threading.Event()
        self.error = ""
        self._proc = None  # keep a reference so the callback isn't garbage-collected

    def start(self) -> bool:
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(3)
        return not self.error

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = 0

    def _run(self) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            mods, vk = parse(self.hotkey)
        except ValueError as e:
            self.error = str(e)
            self._ready.set()
            return
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            self.error = "shortcut is already used by another app - change it in Settings"

        def hook(n_code, w_param, l_param):
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_ESCAPE:
                    try:
                        self.on_escape()
                    except Exception:
                        pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)  # never swallow keys

        self._proc = HOOKPROC(hook)
        hhook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.on_hotkey()
                except Exception:
                    pass
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterHotKey(None, 1)
        if hhook:
            user32.UnhookWindowsHookEx(hhook)
