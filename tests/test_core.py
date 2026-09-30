"""Unit tests for the privacy layer and the routing engine. Run: python -m pytest -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sahay import backends, engine, privacy  # noqa: E402
from sahay.config import Settings  # noqa: E402
from sahay.ocr import OcrLine  # noqa: E402


# ------------------------------------------------------------------ privacy
def test_redacts_indian_and_common_pii():
    text = ("Mail ravi.k@example.com, call +91 98765 43210, PAN ABCDE1234F, "
            "Aadhaar 2345 6789 0123, UPI ravi@okaxis, card 4111 1111 1111 1111, password: hunter2")
    r = privacy.redact(text)
    for token in ["ravi.k@example.com", "98765 43210", "ABCDE1234F", "2345 6789 0123",
                  "ravi@okaxis", "4111 1111 1111 1111", "hunter2"]:
        assert token not in r.text, token
    assert r.found["EMAIL"] == 1 and r.found["PAN"] == 1 and r.found["CARD"] == 1


def test_non_luhn_number_not_treated_as_card():
    r = privacy.redact("Order 1234567890123 shipped")
    assert "CARD" not in r.found


def test_plain_text_unchanged():
    s = "How do I change the wallpaper in Windows 11?"
    assert privacy.redact(s).text == s


def test_sensitive_window_blocks_cloud():
    d = privacy.assess("what is this", "HDFC NetBanking - Microsoft Edge")
    assert not d.allow_cloud and not d.allow_screenshot_to_cloud and d.sensitivity >= 40


def test_normal_context_allows_cloud():
    d = privacy.assess("how do I make a pivot table", "Book1 - Excel")
    assert d.allow_cloud and d.allow_screenshot_to_cloud


# ------------------------------------------------------------------ parsing
def test_parse_reply_json_and_fences():
    d = engine.parse_reply('```json\n{"answer": "Click Save", "point": {"line": 3}}\n```')
    assert d["answer"] == "Click Save" and d["point"]["line"] == 3


def test_parse_reply_plain_text_fallback():
    assert engine.parse_reply("just text")["answer"] == "just text"


class FakeShot:
    left, top, width, height, scale, png = 1920, 0, 2560, 1440, 2.0, b"png"


def test_resolve_pointer_by_line_and_xy():
    lines = [OcrLine(1, "File", 10, 10, 40, 20), OcrLine(2, "Save", 100, 200, 60, 20)]
    p = engine.resolve_pointer({"line": 2, "label": "Save"}, FakeShot, lines)
    assert (p.x, p.y) == (1920 + 130, 210)
    p = engine.resolve_pointer({"x": 100, "y": 50}, FakeShot, lines)
    assert (p.x, p.y) == (1920 + 200, 100)
    assert engine.resolve_pointer({"x": 99999, "y": 1}, FakeShot, lines) is None


# ------------------------------------------------------------------ routing
class Spy(backends.Backend):
    def __init__(self, local, reply='{"answer":"ok","point":null}', fail=False):
        self.is_local, self.reply, self.fail, self.calls = local, reply, fail, []
        self.name = "local-spy" if local else "cloud-spy"

    def chat(self, system, user, image_png=None):
        self.calls.append((user, image_png))
        if self.fail:
            raise backends.BackendError("boom")
        return self.reply


def _engine(monkeypatch, mode, cloud_fail=False, key="k"):
    local, cloud = Spy(True), Spy(False, fail=cloud_fail)
    monkeypatch.setattr(backends, "make_local", lambda s: local)
    monkeypatch.setattr(backends, "make_cloud", lambda s, k: cloud)
    monkeypatch.setattr(engine, "get_api_key", lambda p: key)
    return engine.Engine(Settings(mode=mode)), local, cloud


def test_auto_routes_normal_to_cloud_with_redaction(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "auto")
    r = e.ask("email me at a@b.com how to fix this?", FakeShot, "Notepad")
    # one email in the question -> sensitivity 15 < 40 -> cloud, but redacted
    assert not r.is_local and cloud.calls and not local.calls
    assert "a@b.com" not in cloud.calls[0][0] and r.redactions == 1


def test_auto_keeps_banking_on_device(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "auto")
    r = e.ask("what does this charge mean?", FakeShot, "SBI YONO - Chrome")
    assert r.is_local and local.calls and not cloud.calls


def test_local_mode_never_touches_cloud(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "local")
    e.ask("hello", FakeShot, "Excel")
    assert local.calls and not cloud.calls


def test_cloud_mode_withholds_screenshot_on_sensitive_screen(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "cloud")
    lines = [OcrLine(1, "Balance 45,000 A/c 4111 1111 1111 1111", 0, 0, 10, 10)]
    r = e.ask("explain", FakeShot, "Gmail - Inbox", lines)
    user_text, img = cloud.calls[0]
    assert img is None and "4111 1111 1111 1111" not in user_text and not r.screenshot_sent


def test_cloud_failure_falls_back_to_local(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "cloud", cloud_fail=True)
    r = e.ask("hi", None, "Excel")
    assert r.is_local and r.fell_back and any("on-device" in n for n in r.notes) and e.settings.mode == "cloud"


def test_no_key_uses_local(monkeypatch):
    e, local, cloud = _engine(monkeypatch, "cloud", key="")
    r = e.ask("hi", None, "Excel")
    assert r.is_local and not cloud.calls


# ------------------------------------------------------------------ voice
import numpy as np  # noqa: E402

from sahay.voice.stt import VAD  # noqa: E402
from sahay.voice.tts import clean_for_speech  # noqa: E402


def _run_vad(signal_fn, seconds=6.0, block=0.03):
    vad, t = VAD(no_speech_timeout=3.0), 0.0
    while t < seconds:
        status, _ = vad.feed(signal_fn(t) * np.ones(480, dtype=np.float32), t)
        if status != "wait":
            return status, t
        t += block
    return "wait", t


def test_vad_silence_times_out_at_3s():
    status, t = _run_vad(lambda t: 0.002)
    assert status == "no_speech" and 3.0 <= t < 3.2


def test_vad_speech_then_silence_finishes():
    status, t = _run_vad(lambda t: 0.2 if 0.5 < t < 1.5 else 0.002)
    assert status == "done" and 2.5 < t < 3.0


def test_vad_ignores_single_click():
    status, _ = _run_vad(lambda t: 0.5 if 0.60 < t < 0.62 else 0.002)
    assert status == "no_speech"


def test_clean_for_speech():
    s = clean_for_speech("**Click** `Save`, see https://x.com\n- then [EMAIL_1]")
    assert s == "Click Save, see a link then EMAIL"


# ------------------------------------------------------------------ pointing (UI elements)
def test_pointer_prefers_element_id():
    lines = [OcrLine(1, "Search the web", 500, 20, 200, 30), OcrLine(2, "[Button] Search", 600, 1400, 100, 40)]
    p = engine.resolve_pointer({"id": 2}, FakeShot, lines)
    assert (p.x, p.y) == (1920 + 650, 1420)


def test_pointer_target_name_prefers_real_control_over_text():
    lines = [OcrLine(1, "Search", 500, 20, 60, 30), OcrLine(2, "[Button] Search", 600, 1400, 100, 40)]
    p = engine.resolve_pointer({"target": "Search"}, FakeShot, lines)
    assert p.y == 1420  # the taskbar button at the bottom, not the text at the top


def test_pointer_normalized_coords_for_gemini():
    p = engine.resolve_pointer({"x": 500, "y": 975}, FakeShot, [], normalized=True)
    assert (p.x, p.y) == (1920 + 1280, int(1440 * 0.975))


def test_gemini_gets_normalized_instruction(monkeypatch):
    seen = {}

    class G(backends.GeminiBackend):
        def __init__(self):
            self.model, self.user_model, self.api_key, self.tried = "m", "", "k", []

        def chat(self, system, user, image_png=None):
            seen["system"] = system
            return '{"answer":"ok","point":{"x":500,"y":990}}'

    monkeypatch.setattr(backends, "make_cloud", lambda s, k: G())
    monkeypatch.setattr(engine, "get_api_key", lambda p: "k")
    r = engine.Engine(Settings(mode="cloud")).ask("open notepad", FakeShot, "Desktop")
    assert "0-1000" in seen["system"] and r.pointer.y > 1400


# ------------------------------------------------------------------ cloud robustness
def test_classify_errors():
    assert backends.classify(401, "") is backends.AuthError
    assert backends.classify(400, "API key not valid. Please pass a valid API key.") is backends.AuthError
    assert backends.classify(429, "") is backends.QuotaError
    assert backends.classify(404, "models/gemini-x is not found") is backends.ModelUnavailable
    assert backends.classify(403, "This model is limited to existing users") is backends.ModelUnavailable
    assert backends.classify(500, "boom") is backends.BackendError


def test_cloud_falls_through_retired_models_and_remembers(monkeypatch):
    backends._WORKING.clear()
    calls = []

    def fake(self, model, system, user, image_png):
        calls.append(model)
        if model != "gemini-3.5-flash":
            raise backends.ModelUnavailable("not found")
        return "hi"

    monkeypatch.setattr(backends.GeminiBackend, "_chat_model", fake)
    b = backends.GeminiBackend("key")
    assert b.chat("s", "u") == "hi" and b.model == "gemini-3.5-flash"
    assert calls[:3] == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
    calls.clear()
    backends.GeminiBackend("key").chat("s", "u")
    assert calls == ["gemini-3.5-flash"]  # remembered
    backends._WORKING.clear()


def test_cloud_discovers_models_when_defaults_fail(monkeypatch):
    backends._WORKING.clear()

    def fake(self, model, system, user, image_png):
        if model != "gemini-9.0-flash":
            raise backends.ModelUnavailable("not found")
        return "ok"

    monkeypatch.setattr(backends.GeminiBackend, "_chat_model", fake)
    monkeypatch.setattr(backends.GeminiBackend, "discover", lambda self: ["gemini-9.0-flash"])
    b = backends.GeminiBackend("key")
    assert b.test() == "gemini-9.0-flash"
    backends._WORKING.clear()


def test_auth_error_is_not_retried(monkeypatch):
    calls = []

    def fake(self, model, system, user, image_png):
        calls.append(model)
        raise backends.AuthError("bad key")

    monkeypatch.setattr(backends.OpenAIBackend, "_chat_model", fake)
    try:
        backends.OpenAIBackend("bad").chat("s", "u")
        assert False
    except backends.AuthError:
        pass
    assert len(calls) == 1


def test_network_error_in_cloud_falls_back_to_local(monkeypatch):
    import requests

    class Boom(Spy):
        def chat(self, system, user, image_png=None):
            raise requests.ConnectionError("offline")

    local = Spy(True)
    monkeypatch.setattr(backends, "make_local", lambda s: local)
    monkeypatch.setattr(backends, "make_cloud", lambda s, k: Boom(False))
    monkeypatch.setattr(engine, "get_api_key", lambda p: "k")
    r = engine.Engine(Settings(mode="cloud")).ask("hi", None, "Excel")
    assert r.is_local and r.fell_back and "No internet" in r.notes[0]


def test_pointer_carries_element_size():
    lines = [OcrLine(7, "[Button] Start", 100, 1400, 48, 40)]
    p = engine.resolve_pointer({"id": 7}, FakeShot, lines)
    assert (p.w, p.h) == (48, 40)


# ------------------------------------------------------------------ settings
def test_settings_upgrade_sets_5s_and_clears_retired_model(monkeypatch):
    import json
    import tempfile
    from pathlib import Path

    from sahay import config

    d = Path(tempfile.mkdtemp())
    monkeypatch.setattr(config, "APP_DIR", d)
    monkeypatch.setattr(config, "SETTINGS_FILE", d / "settings.json")
    (d / "settings.json").write_text(json.dumps({"no_speech_timeout": 3.0, "cloud_model": "gemini-2.5-flash",
                                                  "mode": "cloud"}))
    s = config.Settings.load()
    assert s.no_speech_timeout == 5.0 and s.cloud_model == "" and s.mode == "cloud"
    assert json.loads((d / "settings.json").read_text())["settings_version"] == config.SETTINGS_VERSION


def test_vad_default_waits_5_seconds():
    vad, t = VAD(), 0.0
    status = "wait"
    while status == "wait" and t < 10:
        status, _ = vad.feed(0.002 * np.ones(480, dtype=np.float32), t)
        t += 0.03
    assert status == "no_speech" and 5.0 <= t < 5.2
