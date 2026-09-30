"""Main window (opened only from the tray): conversation history + tidy settings."""
from __future__ import annotations

import threading

import requests
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from .. import backends, hardware, ocr, uia
from ..config import get_api_key, set_api_key
from ..voice.tts import VOICES
from . import native, theme
from .widgets import GlowDot, Segmented, Toggle, divider, logo_pixmap, section, setting_row

MODE_OPTIONS = [("local", "On-device"), ("auto", "Auto"), ("cloud", "Cloud")]
MODE_DESC = {
    "local": "Everything stays on this PC. Nothing is sent anywhere.",
    "auto": "Private screens stay on-device. Everything else can use your cloud key, with personal details removed.",
    "cloud": "Uses your cloud key for every request. Personal details are still removed first.",
}
BUBBLE_MAX = 320


def pretty_keys(hotkey: str) -> list[str]:
    out = []
    for part in hotkey.split("+"):
        k = part.strip().strip("<>")
        out.append({"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "space": "Space",
                    "cmd": "Win"}.get(k.lower(), k.upper() if len(k) == 1 else k.title()))
    return out


# ---------------------------------------------------------------------------- chat
class Bubble(QWidget):
    """A chat message. No graphics effects (they glitch inside scroll areas) and a fixed text width,
    so Qt always computes the correct wrapped height (no clipped messages)."""

    def __init__(self, text: str, mine: bool, meta: str = ""):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("userBubble" if mine else "aiBubble")
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self.label = QLabel()
        self.label.setObjectName("bubbleText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label)
        self.meta = QLabel()
        self.meta.setObjectName("bubbleMeta")
        self.meta.setWordWrap(True)
        lay.addWidget(self.meta)
        if mine:
            row.addStretch(1)
            row.addWidget(self.frame)
        else:
            row.addWidget(self.frame)
            row.addStretch(1)
        self.update_content(text, meta)

    def update_content(self, text: str, meta: str = "") -> None:
        self.label.setText(text)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        self.label.ensurePolished()
        fm = self.label.fontMetrics()
        widest = max([fm.horizontalAdvance(line) for line in (text or " ").split("\n")] + [40])
        if meta:
            self.meta.ensurePolished()
            widest = max(widest, min(self.meta.fontMetrics().horizontalAdvance(meta), BUBBLE_MAX))
        w = min(BUBBLE_MAX, widest + 6)
        self.label.setFixedWidth(w)
        self.meta.setFixedWidth(w)
        self.label.setFixedHeight(self.label.heightForWidth(w))
        if meta:
            self.meta.setFixedHeight(self.meta.heightForWidth(w))
        self.frame.adjustSize()
        self.adjustSize()
        self.updateGeometry()


class ChatPage(QWidget):
    ask = Signal(str)
    stop = Signal()

    def __init__(self, hotkey: str):
        super().__init__()
        self.busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.pages = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list = QVBoxLayout(self.body)
        self.list.setContentsMargins(20, 16, 20, 16)
        self.list.setSpacing(10)
        self.list.addStretch(1)  # keeps messages anchored to the bottom, like a chat
        self.scroll.setWidget(self.body)

        # empty state
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(56))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("Just ask.")
        t.setObjectName("emptyTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        keys = QHBoxLayout()
        keys.setSpacing(6)
        keys.addStretch(1)
        for k in pretty_keys(hotkey):
            kl = QLabel(k)
            kl.setObjectName("kbd")
            keys.addWidget(kl)
        keys.addStretch(1)
        d = QLabel("Press the shortcut anywhere and speak.\nStay quiet for a few seconds to type instead.\n"
                   "Press Esc to cancel.")
        d.setObjectName("muted")
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(logo)
        el.addWidget(t)
        el.addLayout(keys)
        el.addWidget(d)
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.scroll)
        lay.addWidget(self.pages, 1)

        # input
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 16)
        bl.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("Message Sahay")
        self.send = QPushButton("↑")
        self.send.setObjectName("send")
        self.send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send.setEnabled(False)
        self.input.textChanged.connect(self._refresh_button)
        self.input.returnPressed.connect(self._submit)
        self.send.clicked.connect(self._clicked)
        bl.addWidget(self.input, 1)
        bl.addWidget(self.send)
        lay.addWidget(bar)

    def _refresh_button(self, *_):
        if self.busy:
            self.send.setText("■")
            self.send.setToolTip("Stop")
            self.send.setEnabled(True)
        else:
            self.send.setText("↑")
            self.send.setToolTip("Send")
            self.send.setEnabled(bool(self.input.text().strip()))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._refresh_button()

    def _clicked(self):
        if self.busy:
            self.stop.emit()
        else:
            self._submit()

    def _submit(self) -> None:
        q = self.input.text().strip()
        if q and not self.busy:
            self.input.clear()
            self.ask.emit(q)

    def add(self, text: str, mine: bool, meta: str = "") -> Bubble:
        self.pages.setCurrentIndex(1)
        b = Bubble(text, mine, meta)
        self.list.addWidget(b)
        self.scroll_to_end()
        return b

    def scroll_to_end(self) -> None:
        def go():
            self.body.adjustSize()
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, go)
        QTimer.singleShot(60, go)


# ------------------------------------------------------------------------ settings
class _Async(QObject):
    done = Signal(str, bool, str)  # which, ok, message


class SettingsPage(QWidget):
    changed = Signal(str)  # name of the setting that changed

    def __init__(self, settings, hw, transcriber):
        super().__init__()
        self.s, self.hw, self.transcriber = settings, hw, transcriber
        self.async_ = _Async()
        self.async_.done.connect(self._async_done)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(20, 4, 20, 24)
        self.lay.setSpacing(0)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        s = self.s

        # --- Privacy -------------------------------------------------------------
        self._section("Privacy")
        self.mode = Segmented(MODE_OPTIONS, s.mode)
        self.mode.changed.connect(self._mode_changed)
        self.mode_desc = QLabel(MODE_DESC[s.mode])
        self.mode_desc.setObjectName("rowDesc")
        self.mode_desc.setWordWrap(True)
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 14, 0, 12)
        wl.setSpacing(8)
        wl.addWidget(self.mode)
        wl.addWidget(self.mode_desc)
        self._add(w, first=True)
        self._add(setting_row("Look at my screen", "Lets Sahay see what you see so it can point.",
                              self._toggle("send_screenshot")))
        self._add(setting_row("Remove personal details", "Masks emails, phone, Aadhaar, PAN, UPI and card "
                                                         "numbers before anything goes to the cloud.",
                              self._toggle("redact_before_cloud")))
        self._add(setting_row("Auto mode strictness", "Further left keeps more things on-device.",
                              self._slider("sensitivity_threshold", 10, 90, lambda v: f"{v}")))

        # --- Voice ---------------------------------------------------------------
        self._section("Voice")
        self._add(setting_row("Speak to ask", "Speech recognition runs on this PC (Whisper).",
                              self._toggle("voice_input")), first=True)
        self._add(setting_row("Wait for speech", "Seconds of silence before the text box appears.",
                              self._slider("no_speech_timeout", 3, 10, lambda v: f"{v} s", as_float=True)))
        model = Segmented([("tiny.en", "Fast"), ("base.en", "Accurate")], s.stt_model)
        model.changed.connect(lambda k: self._set("stt_model", k))
        self.stt_status = QLabel("")
        self.stt_status.setObjectName("rowDesc")
        self.stt_status.setWordWrap(True)
        sw = QWidget()
        swl = QVBoxLayout(sw)
        swl.setContentsMargins(0, 0, 0, 0)
        swl.setSpacing(0)
        swl.addWidget(setting_row("Speech model", "", model))
        swl.addWidget(self.stt_status)
        swl.addSpacing(10)
        self._add(sw)
        self._add(setting_row("Read answers aloud", "", self._toggle("voice_output")))
        voice = QComboBox()
        for key, label in VOICES:
            voice.addItem(label, key)
        voice.setCurrentIndex(max(0, voice.findData(s.tts_voice)))
        voice.currentIndexChanged.connect(lambda _: self._set("tts_voice", voice.currentData()))
        voice.setFixedWidth(210)
        self._add(setting_row("Voice", "Natural Microsoft Edge voice.", voice))
        eng = Segmented([("auto", "Smart"), ("edge", "Edge"), ("offline", "Offline")], s.tts_engine)
        eng.changed.connect(lambda k: self._set("tts_engine", k))
        self._add(setting_row("Voice engine", "Smart reads on-device answers with the offline Windows voice, "
                                             "so private answers never leave the PC.", eng, stacked=True))

        # --- Cloud ---------------------------------------------------------------
        self._section("Cloud AI")
        prov = Segmented([("gemini", "Gemini"), ("openai", "OpenAI"), ("anthropic", "Anthropic")],
                         s.cloud_provider)
        self._add(setting_row("Provider", "", prov), first=True)
        kw = QWidget()
        kl = QHBoxLayout(kw)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(6)
        self.key = QLineEdit(get_api_key(s.cloud_provider))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste API key")
        self.key.setFixedWidth(176)
        eye = QPushButton("👁")
        eye.setObjectName("ghost")
        eye.setFixedWidth(34)
        eye.setCheckable(True)
        eye.setCursor(Qt.CursorShape.PointingHandCursor)
        eye.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        kl.addWidget(self.key)
        kl.addWidget(eye)
        self._key_timer = QTimer(self)
        self._key_timer.setSingleShot(True)
        self._key_timer.timeout.connect(self._save_key)
        self.key.textEdited.connect(lambda _: self._key_timer.start(500))  # saves as you paste/type
        self.key.editingFinished.connect(self._save_key)
        self._add(setting_row("API key", "Encrypted with your Windows account.", kw))
        self.cloud_model = QLineEdit(s.cloud_model)
        self.cloud_model.setPlaceholderText("Automatic")
        self.cloud_model.setFixedWidth(210)
        self.cloud_model.editingFinished.connect(lambda: self._set("cloud_model", self.cloud_model.text().strip()))
        self._add(setting_row("Model", "Leave on Automatic - Sahay finds a model your key can use.",
                              self.cloud_model))
        self.cloud_result = self._test_row("Test cloud", self._test_cloud)

        def provider_changed(k):
            self._set("cloud_provider", k)
            self.key.setText(get_api_key(k))
            self.cloud_model.setText("")
            self._set("cloud_model", "")
            self.cloud_result.setText("")

        prov.changed.connect(provider_changed)

        # --- On-device -----------------------------------------------------------
        self._section("On-device AI")
        self.engine_seg = Segmented([("ollama", "Ollama"), ("openai_compat", "Foundry Local (NPU)")],
                                    s.local_engine)
        self.engine_seg.changed.connect(lambda k: self._set("local_engine", k))
        self._add(setting_row("Engine", "Foundry Local runs QNN-optimised models on the Snapdragon "
                                        "Hexagon NPU.", self.engine_seg, stacked=True), first=True)
        self.npu_result = self._test_row("Use Snapdragon NPU", self._detect_npu, add_divider=True,
                                         desc="Finds Microsoft Foundry Local and picks an NPU model.")
        self.url = QLineEdit(s.local_url)
        self.url.setFixedWidth(210)
        self.url.editingFinished.connect(lambda: self._set("local_url", self.url.text().strip()))
        self._add(setting_row("Server", "", self.url))
        self.lm = QLineEdit(s.local_model)
        self.lm.setFixedWidth(210)
        self.lm.editingFinished.connect(lambda: self._set("local_model", self.lm.text().strip()))
        self._add(setting_row("Model", "", self.lm))
        self.vision_toggle = self._toggle("local_model_has_vision")
        self._add(setting_row("Model can see images", "Off for text-only (NPU) models - Sahay then reads "
                                                      "the screen's controls and text on-device instead.",
                              self.vision_toggle))
        self.local_result = self._test_row("Test on-device AI", self._test_local)

        # --- Pointing & general -------------------------------------------------
        self._section("Pointing")
        self._add(setting_row("Point at things", "Fly the cursor buddy to what you should click.",
                              self._toggle("show_pointer")), first=True)
        self._add(setting_row("Highlight with a box", "Draw a glowing box around the target.",
                              self._toggle("show_highlight")))

        self._section("General")
        hk = QLineEdit(s.hotkey)
        hk.setFixedWidth(210)
        hk.editingFinished.connect(lambda: self._set("hotkey", hk.text().strip() or s.hotkey))
        self._add(setting_row("Shortcut", "Format: <ctrl>+<alt>+<space>", hk), first=True)
        self._add(setting_row("Battery saver mode", "Lighter 30 fps animations when Windows battery saver is on.",
                              self._toggle("battery_saver_fps")))

        # --- This PC -------------------------------------------------------------
        self._section("This PC")
        rows = list(hw.details())
        rows.append(("Screen reading", ("UI Automation" if uia.available() else "UI Automation not installed")
                     + (" + OCR" if ocr.available() else ""), uia.available()))
        first = True
        for label, value, good in rows:
            iw = QWidget()
            il = QHBoxLayout(iw)
            il.setContentsMargins(0, 10, 0, 10)
            il.setSpacing(10)
            il.addWidget(GlowDot(theme.GREEN if good else theme.AMBER), 0, Qt.AlignmentFlag.AlignVCenter)
            t = QLabel(label)
            t.setObjectName("rowTitle")
            v = QLabel(value)
            v.setObjectName("rowDesc")
            v.setWordWrap(True)
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            il.addWidget(t)
            il.addWidget(v, 1)
            self._add(iw, first=first)
            first = False
        self.lay.addStretch(1)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)

    # live status only while the page is on screen (no idle timers in the tray)
    def showEvent(self, e):
        self._status_timer.start()
        self._refresh_status()
        super().showEvent(e)

    def hideEvent(self, e):
        self._status_timer.stop()
        super().hideEvent(e)

    # ------------------------------------------------------------------ helpers
    def _section(self, title):
        label, self._card, self._card_lay = section(title)
        self.lay.addWidget(label)
        self.lay.addWidget(self._card)

    def _add(self, w, first=False):
        if not first:
            self._card_lay.addWidget(divider())
        self._card_lay.addWidget(w)

    def _toggle(self, name) -> Toggle:
        t = Toggle(bool(getattr(self.s, name)))
        t.toggled.connect(lambda v: self._set(name, bool(v)))
        return t

    def _slider(self, name, lo, hi, fmt, as_float=False) -> QWidget:
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(10)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(int(round(getattr(self.s, name))))
        sl.setFixedWidth(130)
        val = QLabel(fmt(sl.value()))
        val.setObjectName("rowDesc")
        val.setFixedWidth(34)
        sl.valueChanged.connect(lambda v: (val.setText(fmt(v)), self._set(name, float(v) if as_float else int(v))))
        l.addWidget(sl)
        l.addWidget(val)
        return w

    def _test_row(self, text, fn, add_divider=True, desc="") -> QLabel:
        btn = QPushButton(text)
        btn.setObjectName("ghost")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        res = QLabel(desc)
        res.setObjectName("rowDesc")
        res.setWordWrap(True)
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 12, 0, 12)
        l.setSpacing(12)
        l.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        l.addWidget(res, 1)
        btn.clicked.connect(fn)
        self._add(w, first=not add_divider)
        return res

    def _set(self, name, value):
        if getattr(self.s, name) != value:
            setattr(self.s, name, value)
            self.s.save()
            self.changed.emit(name)

    def _mode_changed(self, k):
        self.mode_desc.setText(MODE_DESC[k])
        self._set("mode", k)

    def set_mode(self, k):
        self.mode.set(k)
        self.mode_desc.setText(MODE_DESC[k])

    def _save_key(self):
        key = self.key.text().strip()
        if key == get_api_key(self.s.cloud_provider):
            return
        if set_api_key(self.s.cloud_provider, key):
            self.cloud_result.setStyleSheet(f"color: {theme.GREEN};")
            self.cloud_result.setText("Key saved." if key else "Key removed.")
        else:
            self.cloud_result.setStyleSheet(f"color: {theme.RED};")
            self.cloud_result.setText("Could not save the key.")
        self.changed.emit("api_key")

    def _refresh_status(self):
        if not self.isVisible():
            return
        t = self.transcriber
        eng = t.engine or "loading…"
        st = t.status if t.status not in ("ready",) else "ready"
        self.stt_status.setText(f"{eng} · {st}")

    def _run(self, which, fn, label: QLabel, busy_text):
        label.setStyleSheet(f"color: {theme.MUTED};")
        label.setText(busy_text)

        def work():
            try:
                ok, msg = fn()
            except Exception as e:
                ok, msg = False, backends.friendly(e)
            self.async_.done.emit(which, ok, msg)

        threading.Thread(target=work, daemon=True).start()

    def _async_done(self, which, ok, msg):
        label = {"cloud": self.cloud_result, "local": self.local_result, "npu": self.npu_result}[which]
        label.setStyleSheet(f"color: {theme.GREEN if ok else theme.AMBER};")
        label.setText(msg)
        if which == "npu" and ok:
            self.engine_seg.set("openai_compat")
            self.url.setText(self.s.local_url)
            self.lm.setText(self.s.local_model)
            self.vision_toggle.setChecked(False)

    def _test_cloud(self):
        self._save_key()
        key = get_api_key(self.s.cloud_provider)
        if not key:
            self.cloud_result.setStyleSheet(f"color: {theme.AMBER};")
            self.cloud_result.setText("Paste an API key first.")
            return

        def fn():
            b = backends.make_cloud(self.s, key)
            model = b.test()
            return True, f"Connected ✓  using {model}"

        self._run("cloud", fn, self.cloud_result, "Testing…")

    def _test_local(self):
        s = self.s

        def fn():
            base = s.local_url.rstrip("/")
            if s.local_engine == "ollama":
                r = requests.get(base + "/api/tags", timeout=4)
                names = [m.get("name", "") for m in r.json().get("models", [])]
                found = any(n == s.local_model or n.split(":")[0] == s.local_model.split(":")[0] for n in names)
                return found, (f"Connected ✓  {s.local_model} ready" if found else
                               f"Connected, but '{s.local_model}' isn't downloaded. Run: ollama pull {s.local_model}")
            url = (base if base.endswith("/v1") else base + "/v1") + "/models"
            r = requests.get(url, timeout=4)
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            return (s.local_model in ids), ("Connected ✓" if s.local_model in ids else
                                            f"Connected. Models: {', '.join(ids[:4]) or 'none'}")

        self._run("local", fn, self.local_result, "Testing…")

    def _detect_npu(self):
        s = self.s

        def fn():
            res = hardware.detect_foundry_local()
            if res["ok"]:
                s.local_engine = "openai_compat"
                s.local_url = res["url"]
                s.local_model = res["models"][0]
                s.local_model_has_vision = False
                s.save()
            return res["ok"], res["message"]

        self._run("npu", fn, self.npu_result, "Looking for Foundry Local…")


# -------------------------------------------------------------------------- window
class MainWindow(QWidget):
    ask = Signal(str)
    stop = Signal()
    settings_changed = Signal(str)

    def __init__(self, settings, hw, icon, transcriber):
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Sahay")
        self.setWindowIcon(icon)
        self.resize(470, 700)
        self.setMinimumSize(420, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 14, 20, 0)
        hl.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(24))
        brand = QLabel("Sahay")
        brand.setObjectName("brand")
        hl.addWidget(logo)
        hl.addWidget(brand)
        hl.addStretch(1)
        self.status_dot = GlowDot(theme.GREEN)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        hl.addWidget(self.status_dot)
        hl.addWidget(self.status)
        lay.addWidget(head)

        tabs = QWidget()
        tl = QHBoxLayout(tabs)
        tl.setContentsMargins(20, 10, 20, 0)
        tl.setSpacing(0)
        self.tab_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.chat = ChatPage(settings.hotkey)
        self.settings_page = SettingsPage(settings, hw, transcriber)
        for i, name in enumerate(("Chat", "Settings")):
            b = QPushButton(name)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
            self.tab_group.addButton(b, i)
            tl.addWidget(b)
        tl.addStretch(1)
        lay.addWidget(tabs)
        line = QFrame()
        line.setObjectName("divider")
        lay.addWidget(line)

        self.stack.addWidget(self.chat)
        self.stack.addWidget(self.settings_page)
        lay.addWidget(self.stack, 1)

        self.toast = QLabel("Saved", self)
        self.toast.setObjectName("toast")
        self.toast.adjustSize()
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)

        self.chat.ask.connect(self.ask)
        self.chat.stop.connect(self.stop)
        self.settings_page.changed.connect(self._on_setting)
        self.update_status(settings)

    def _on_setting(self, name):
        self.toast.setText("Saved")
        self.toast.adjustSize()
        self.toast.move((self.width() - self.toast.width()) // 2, self.height() - 64)
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start(1100)
        self.settings_changed.emit(name)

    def update_status(self, s):
        label = {"local": "On-device", "auto": "Auto", "cloud": "Cloud"}[s.mode]
        self.status.setText(label)
        self.status_dot.set_color({"local": theme.GREEN, "auto": theme.ACCENT, "cloud": theme.ACCENT2}[s.mode])
        self.settings_page.set_mode(s.mode)

    def show_tab(self, i: int):
        self.tab_group.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)

    def present(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        native.dark_title_bar(self)
        self.raise_()
        self.activateWindow()
        native.force_foreground(self)
        self.chat.scroll_to_end()

    def closeEvent(self, e):
        e.ignore()
        self.hide()
