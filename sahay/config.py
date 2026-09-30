"""Settings (JSON in %APPDATA%\\Sahay) and API keys.

API keys are stored in Windows Credential Manager (keyring) AND in a DPAPI-encrypted file
(tied to your Windows user account) as a fallback, so a key you paste is never lost and
is never stored in plain text.
"""
from __future__ import annotations

import base64
import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Sahay"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", APP_DIR)) / "Sahay"   # models, cache
SETTINGS_FILE = APP_DIR / "settings.json"
KEYS_FILE = APP_DIR / "keys.dat"
KEYRING_SERVICE = "Sahay"
SETTINGS_VERSION = 3

MODES = ("local", "auto", "cloud")
CLOUD_PROVIDERS = ("gemini", "openai", "anthropic")
# Old defaults that no longer work for new API keys - cleared on upgrade.
_STALE_MODELS = {"gemini-2.5-flash", "gemini-2.0-flash", "gpt-4.1-mini", "claude-sonnet-4-5"}


@dataclass
class Settings:
    settings_version: int = SETTINGS_VERSION
    mode: str = "auto"                 # local | auto | cloud
    cloud_provider: str = "gemini"      # gemini | openai | anthropic
    cloud_model: str = ""               # blank = best available (auto-detected)
    # Local engine:
    #   "ollama"        -> Ollama (native Windows ARM64 + x64). Vision models supported.
    #   "openai_compat" -> any OpenAI-compatible local server, e.g. Microsoft Foundry Local
    #                      (runs QNN / Hexagon NPU builds of models on Snapdragon), LM Studio, llama.cpp.
    local_engine: str = "ollama"
    local_url: str = "http://127.0.0.1:11434"
    local_model: str = "qwen2.5vl:3b"
    local_model_has_vision: bool = True  # False -> Sahay sends on-device UI elements + OCR text instead
    hotkey: str = "<ctrl>+<alt>+<space>"
    send_screenshot: bool = True
    redact_before_cloud: bool = True
    sensitivity_threshold: int = 40
    show_pointer: bool = True
    show_highlight: bool = True
    screenshot_max_side: int = 1280
    # voice
    voice_input: bool = True
    stt_model: str = "base.en"          # on-device Whisper size: tiny.en (fastest) | base.en (more accurate)
    no_speech_timeout: float = 5.0      # seconds without speech before the text box appears
    voice_output: bool = True
    tts_engine: str = "auto"            # auto (Edge for cloud answers, offline for on-device) | edge | offline
    tts_voice: str = "en-IN-NeerjaNeural"
    tts_rate: str = "+5%"
    # efficiency
    battery_saver_fps: bool = True      # halve animation frame rate on battery saver

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        if int(data.get("settings_version", 0)) < SETTINGS_VERSION:
            s.no_speech_timeout = max(float(s.no_speech_timeout), 5.0)
            if s.cloud_model in _STALE_MODELS:
                s.cloud_model = ""
            s.settings_version = SETTINGS_VERSION
            s.save()
        return s

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# ------------------------------------------------------------------ DPAPI (Windows)
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "Sahay", None, None, None, 0x1, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _file_keys() -> dict:
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _file_get(provider: str) -> str:
    if platform.system() != "Windows":
        return ""
    enc = _file_keys().get(provider)
    if not enc:
        return ""
    try:
        return _dpapi(base64.b64decode(enc), protect=False).decode("utf-8")
    except Exception:
        return ""


def _file_set(provider: str, key: str) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        keys = _file_keys()
        if key:
            keys[provider] = base64.b64encode(_dpapi(key.encode("utf-8"), protect=True)).decode("ascii")
        else:
            keys.pop(provider, None)
        APP_DIR.mkdir(parents=True, exist_ok=True)
        KEYS_FILE.write_text(json.dumps(keys), encoding="utf-8")
        return True
    except Exception:
        return False


def get_api_key(provider: str) -> str:
    env = os.environ.get(f"SAHAY_{provider.upper()}_API_KEY")
    if env:
        return env.strip()
    try:
        import keyring  # type: ignore

        k = keyring.get_password(KEYRING_SERVICE, provider)
        if k:
            return k.strip()
    except Exception:
        pass
    return _file_get(provider).strip()


def set_api_key(provider: str, key: str) -> bool:
    key = (key or "").strip()
    ok_ring = False
    try:
        import keyring  # type: ignore

        if key:
            keyring.set_password(KEYRING_SERVICE, provider, key)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, provider)
            except Exception:
                pass
        ok_ring = True
    except Exception:
        pass
    ok_file = _file_set(provider, key)
    return ok_ring or ok_file
