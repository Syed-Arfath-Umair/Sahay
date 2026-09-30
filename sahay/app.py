"""Sahay controller: lives in the system tray, reacts to the global hotkey.

Hotkey -> buddy + "Listening" -> (speech) -> "Analyzing" -> "Answering" (spoken; buddy flies to the
          target and a glowing box highlights it)
       -> (no speech for N s) -> glass text box -> same flow
Cancel at any time: Esc, the hotkey again, the tray menu, or Stop in the chat window.
"""
from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__, backends, capture, hardware, ocr, uia
from .config import Settings
from .engine import Engine
from .ui import native, theme
from .ui.highlight import Highlight
from .ui.overlay import Overlay
from .ui.typebox import TypeBox
from .ui.widgets import logo_icon
from .ui.window import MainWindow, pretty_keys
from .voice import stt
from .voice.tts import Speaker


class Bridge(QObject):
    """Thread -> UI signals (Qt queues these onto the main thread)."""
    hotkey = Signal()
    escape = Signal()
    level = Signal(float)
    heard = Signal(int, object)           # session, audio or None
    transcribed = Signal(int, str)
    answered = Signal(int, object, bool)  # session, Result, from_window
    failed = Signal(int, str, bool)
    spoken = Signal(int)


def meta_line(r) -> str:
    bits = ["🔒 On-device" if r.is_local else "☁ Cloud", r.route.split(" - ", 1)[-1], f"{r.seconds:.1f}s"]
    if r.redactions:
        bits.append(f"{r.redactions} detail{'s' if r.redactions > 1 else ''} hidden")
    if not r.is_local:
        bits.append("screen shared" if r.screenshot_sent else "screen not shared")
    line = "  ·  ".join(bits)
    if r.notes:
        line += "\n" + "\n".join(r.notes)
    return line


class Controller:
    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        self.engine = Engine(self.settings)
        self.hw = hardware.detect()
        self.bridge = Bridge()
        self.icon = logo_icon()
        self.speaker = Speaker()
        self.transcriber = stt.Transcriber(self.settings.stt_model)
        self.voice_ok, self.voice_why = stt.available()
        if self.voice_ok and self.settings.voice_input:
            self.transcriber.preload()

        self.overlay = Overlay()
        self.highlight = Highlight()
        self.typebox = TypeBox()
        self.window = MainWindow(self.settings, self.hw, self.icon, self.transcriber)

        self.state = "idle"
        self.session = 0
        self.ctx = (None, "")
        self.targets = {"done": threading.Event(), "items": []}
        self.stop_listen = threading.Event()
        self.hotkeys = None
        self.esc_listener = None
        self.win_session = 0
        self._pending = None

        b = self.bridge
        b.hotkey.connect(self.on_hotkey)
        b.escape.connect(self.on_escape)
        b.level.connect(self.overlay.set_level)
        b.heard.connect(self.on_heard)
        b.transcribed.connect(self.on_transcribed)
        b.answered.connect(self.on_answered)
        b.failed.connect(self.on_failed)
        b.spoken.connect(self.on_spoken)
        self.typebox.submitted.connect(self.on_typed)
        self.typebox.cancelled.connect(self.cancel)
        self.window.ask.connect(self.ask_from_window)
        self.window.stop.connect(self.stop_window_query)
        self.window.settings_changed.connect(self.on_setting)

        self._make_tray()
        self._start_hotkey()
        self._start_esc_listener()
        hardware.use_efficiency_qos()   # idle in the tray on efficiency cores

    # ------------------------------------------------------------------ tray / keys
    def _hotkey_text(self) -> str:
        return " + ".join(pretty_keys(self.settings.hotkey))

    def _make_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(f"Sahay · {self._hotkey_text()}")
        menu = QMenu()
        a_open = QAction("Open Sahay", menu)
        a_open.triggered.connect(lambda: self.open_window(0))
        self.a_ask = QAction(f"Ask   {self._hotkey_text()}", menu)
        self.a_ask.triggered.connect(lambda: QTimer.singleShot(250, self.on_hotkey))
        self.a_cancel = QAction("Cancel current question   Esc", menu)
        self.a_cancel.triggered.connect(self.cancel)
        self.a_cancel.setEnabled(False)
        a_set = QAction("Settings", menu)
        a_set.triggered.connect(lambda: self.open_window(1))
        a_quit = QAction("Quit Sahay", menu)
        a_quit.triggered.connect(self.quit)
        for a in (a_open, self.a_ask, self.a_cancel, a_set):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(a_quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()
        self.tray.showMessage("Sahay is ready", f"Press {self._hotkey_text()} anywhere and just ask.",
                              self.icon, 3500)

    def _tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.window.isVisible() and self.window.isActiveWindow():
                self.window.hide()
            else:
                self.open_window(0)

    def open_window(self, tab: int) -> None:
        self.window.show_tab(tab)
        self.window.present()

    def _start_hotkey(self) -> None:
        """Native Windows hotkey + Esc (no third-party keyboard library)."""
        try:
            from .hotkeys import NativeHotkeys

            if self.hotkeys:
                self.hotkeys.stop()

            def esc():
                if self.state != "idle":
                    self.bridge.escape.emit()

            self.hotkeys = NativeHotkeys(self.settings.hotkey, self.bridge.hotkey.emit, esc)
            if not self.hotkeys.start():
                print("Hotkey problem:", self.hotkeys.error)
                self.tray.showMessage("Sahay", f"Shortcut problem: {self.hotkeys.error}", self.icon, 5000)
        except Exception as e:
            print("Global hotkey unavailable:", e)

    def _start_esc_listener(self) -> None:
        pass  # Esc is handled by the native hook in _start_hotkey

    def _set_state(self, state: str) -> None:
        self.state = state
        self.a_cancel.setEnabled(state != "idle")

    # ------------------------------------------------------------------ flow
    def on_hotkey(self) -> None:
        if self.state == "idle":
            self.start_session()
        elif self.state == "listening":
            self.stop_listen.set()        # finish talking now
        else:
            self.cancel()

    def on_escape(self) -> None:
        if self.state != "idle":
            self.cancel()

    def _apply_power_profile(self) -> None:
        ps = hardware.power_state()
        ms = 33 if (self.settings.battery_saver_fps and ps["saver"]) else 16
        self.overlay.set_interval(ms)
        self.highlight.set_interval(ms)

    def start_session(self) -> None:
        self.session += 1
        sid = self.session
        hardware.use_high_qos()
        self.speaker.stop()
        self.highlight.hide_now()
        self._apply_power_profile()
        title = capture.active_window_title()
        fg = capture.foreground_hwnd()
        shot = None
        if self.settings.send_screenshot:
            try:
                shot = capture.capture(self.settings.screenshot_max_side)
            except Exception as e:
                print("Screenshot failed:", e)
        self.ctx = (shot, title)

        # Read the screen (UI controls + OCR text) in the background while the user talks.
        box = {"done": threading.Event(), "items": []}
        self.targets = box

        def read_screen():
            items = []
            try:
                if shot is not None:
                    items = uia.collect(fg, shot)
                    text = ocr.recognize(shot.image, max_lines=120)
                    for i, ln in enumerate(text):
                        ln.id = len(items) + i + 1
                    items += text
            except Exception as e:
                print("Screen reading failed:", e)
            box["items"] = items
            box["done"].set()

        threading.Thread(target=read_screen, daemon=True).start()

        # Speculative warm-up: load the on-device model into memory while the user is speaking.
        if self.settings.mode != "cloud":
            threading.Thread(target=lambda: backends.make_local(self.settings).warmup(), daemon=True).start()

        if self.voice_ok and self.settings.voice_input:
            self._set_state("listening")
            self.overlay.listening()
            self.stop_listen = threading.Event()
            stop = self.stop_listen

            def listen():
                try:
                    audio = stt.record(stop, self.bridge.level.emit, self.settings.no_speech_timeout)
                except Exception as e:
                    print("Microphone error:", e)
                    audio = None
                self.bridge.heard.emit(sid, audio)

            threading.Thread(target=listen, daemon=True).start()
        else:
            self.to_typing()

    def to_typing(self) -> None:
        self._set_state("typing")
        self.overlay.typing()
        sid = self.session
        QTimer.singleShot(120, lambda: sid == self.session and self.state == "typing" and
                          self.typebox.open_at(self.overlay.tip_global(), self.overlay.flip))

    def on_heard(self, sid: int, audio) -> None:
        if sid != self.session or self.state != "listening":
            return
        if audio is None:
            self.to_typing()
            return
        self._set_state("analyzing")
        self.overlay.analyzing("")

        def work():
            try:
                text = self.transcriber.transcribe(audio)
            except Exception as e:
                self.bridge.failed.emit(sid, f"Speech recognition failed: {e}", False)
                return
            self.bridge.transcribed.emit(sid, text)

        threading.Thread(target=work, daemon=True).start()

    def on_transcribed(self, sid: int, text: str) -> None:
        if sid != self.session or self.state == "idle":
            return
        if len(text.strip()) < 2 or text.strip().lower() in ("[blank_audio]", "(silence)", "you"):
            self.overlay.message("Didn't catch that - type instead?", 1.4)
            QTimer.singleShot(1300, lambda: sid == self.session and self.to_typing())
            return
        self.ask(sid, text, voice=True)

    def on_typed(self, text: str) -> None:
        if self.state != "typing":
            return
        self.ask(self.session, text, voice=True)

    def ask(self, sid: int, question: str, voice: bool) -> None:
        self._set_state("analyzing")
        self.overlay.analyzing(question)
        self.window.chat.add(question, mine=True)
        shot, title = self.ctx
        box = self.targets

        def work():
            try:
                box["done"].wait(6)
                r = self.engine.ask(question, shot, title, box["items"], voice=voice)
                self.bridge.answered.emit(sid, r, False)
            except Exception as e:
                self.bridge.failed.emit(sid, backends.friendly(e), False)

        threading.Thread(target=work, daemon=True).start()

    def on_answered(self, sid: int, r, from_window: bool) -> None:
        if from_window:
            self._window_answer(sid, r)
            return
        if sid != self.session:
            return  # cancelled - drop silently
        self.window.chat.add(r.answer, mine=False, meta=meta_line(r))
        self._set_state("answering")
        label = r.pointer.label if r.pointer else ""
        self.overlay.answering(label or ("answered on-device" if r.fell_back else ""))
        if r.pointer:
            center = native.phys_to_logical(r.pointer.x, r.pointer.y)
            dur = self.overlay.fly_to(center, label)
            if self.settings.show_highlight:
                w, h = r.pointer.w or 36, r.pointer.h or 28
                tl = native.phys_to_logical(r.pointer.x - w // 2, r.pointer.y - h // 2)
                br = native.phys_to_logical(r.pointer.x + w // 2, r.pointer.y + h // 2)
                self.highlight.show_rect(QRectF(tl, br), theme.C_ANSWER, delay_ms=int(dur * 850))
        if self.settings.voice_output:
            eng = self.settings.tts_engine
            online = eng == "edge" or (eng == "auto" and not r.is_local)
            self.speaker.speak(r.answer, online, self.settings.tts_voice, self.settings.tts_rate,
                               on_done=lambda: self.bridge.spoken.emit(sid))
        else:
            self.open_window(0)
            QTimer.singleShot(3000, lambda: self.bridge.spoken.emit(sid))

    def on_spoken(self, sid: int) -> None:
        if sid != self.session or self.state != "answering":
            return
        self._set_state("idle")
        hardware.use_efficiency_qos()
        pointed = self.overlay.arrived_at is not None or self.overlay.flight is not None
        linger = 3500 if pointed else 800

        def done():
            if sid == self.session and self.state == "idle":
                self.overlay.dismiss()
                self.highlight.dismiss()

        QTimer.singleShot(linger, done)

    def on_failed(self, sid: int, msg: str, from_window: bool) -> None:
        print("Error:", msg)
        if from_window:
            self._window_answer(sid, None, msg)
            return
        if sid != self.session:
            return
        self.window.chat.add(f"Something went wrong: {msg}", mine=False)
        self._set_state("idle")
        hardware.use_efficiency_qos()
        low = msg.lower()
        if "not running" in low or "not reachable" in low:
            short = "On-device AI isn't running"
        elif "key" in low and "reject" in low:
            short = "Cloud key rejected - check Settings"
        elif "speech" in low:
            short = "Couldn't hear that"
        else:
            short = "Something went wrong - see Sahay window"
        self.overlay.message(short, 3.0)

    def cancel(self) -> None:
        was_busy = self.state != "idle"
        self.session += 1
        self.stop_listen.set()
        self.speaker.stop()
        self.typebox.close_box()
        self.highlight.dismiss()
        self._set_state("idle")
        hardware.use_efficiency_qos()
        if was_busy:
            self.overlay.message("Cancelled", 0.8, theme.C_TYPE)
        elif self.overlay.state != "message":
            self.overlay.dismiss()

    # ------------------------------------------------------------------ window chat (text only)
    def ask_from_window(self, question: str) -> None:
        self.win_session += 1
        wid = self.win_session
        self.window.chat.add(question, mine=True)
        self._pending = self.window.chat.add("…", mine=False)
        self.window.chat.set_busy(True)
        hardware.use_high_qos()

        def work():
            try:
                r = self.engine.ask(question, None, "Sahay window", [], voice=False)
                self.bridge.answered.emit(wid, r, True)
            except Exception as e:
                self.bridge.failed.emit(wid, backends.friendly(e), True)

        threading.Thread(target=work, daemon=True).start()

    def stop_window_query(self) -> None:
        self.win_session += 1
        if self._pending is not None:
            self._pending.update_content("Stopped.", "")
            self._pending = None
        self.window.chat.set_busy(False)

    def _window_answer(self, wid: int, r, err: str = "") -> None:
        if wid != self.win_session or self._pending is None:
            return  # stopped
        if r is None:
            self._pending.update_content(f"Something went wrong: {err}", "")
        else:
            self._pending.update_content(r.answer, meta_line(r))
        self._pending = None
        self.window.chat.set_busy(False)
        self.window.chat.scroll_to_end()

    # ------------------------------------------------------------------ settings
    def on_setting(self, name: str) -> None:
        if name == "hotkey":
            self._start_hotkey()
            self.tray.setToolTip(f"Sahay · {self._hotkey_text()}")
            self.a_ask.setText(f"Ask   {self._hotkey_text()}")
        if name == "mode":
            self.window.update_status(self.settings)
        if name == "stt_model":
            self.transcriber.set_model(self.settings.stt_model)
        if name == "voice_input" and self.settings.voice_input and self.voice_ok:
            self.transcriber.preload()

    def quit(self) -> None:
        self.cancel()
        for l in (self.hotkeys,):
            try:
                if l:
                    l.stop()
            except Exception:
                pass
        self.tray.hide()
        self.app.quit()


def main() -> None:
    if sys.stdout is None or sys.stderr is None:  # pythonw: send output to a log file
        from .config import APP_DIR

        APP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(APP_DIR / "sahay.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Sahay")
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.QSS)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available.")
    c = Controller(app)
    print(f"Sahay {__version__} running in the tray. Hotkey: {c.settings.hotkey}. {c.hw.summary()}")
    if not c.voice_ok:
        print("Voice input disabled:", c.voice_why)
    sys.exit(app.exec())
