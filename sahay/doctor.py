"""Sahay doctor: a readiness checklist for this PC.   Run:  python -m sahay.doctor

Shows whether Sahay runs natively on Snapdragon (ARM64), which AI accelerators are available,
and whether each component (voice, screen reading, local AI, cloud) is ready.
"""
from __future__ import annotations

import sys

import requests

from . import __version__, hardware, ocr, uia
from .config import CLOUD_PROVIDERS, Settings, get_api_key
from .voice import stt

OK, WARN, BAD = "OK ", "!! ", "-- "


def line(status: str, label: str, detail: str) -> None:
    print(f"  [{status}] {label:<22} {detail}")


def main() -> int:
    s = Settings.load()
    hw = hardware.detect()
    print(f"\nSahay {__version__} - system check\n")

    print("Hardware")
    line(OK, "Processor", hw.cpu_name)
    line(OK if hw.is_snapdragon else WARN, "Snapdragon", "yes - Windows on ARM64" if hw.is_snapdragon else
         "no (Sahay still works; NPU features need a Snapdragon PC)")
    if hw.native_arch == "ARM64":
        line(OK if hw.python_native else BAD, "Native ARM64 Python",
             "yes" if hw.python_native else "NO - x64 Python is emulated. Install the ARM64 Python build.")
    line(OK if hw.best_provider != "CPUExecutionProvider" else WARN, "ONNX Runtime", hw.accelerator)
    if hw.providers_available:
        line(OK, "Execution providers", ", ".join(hw.providers_available))
    ps = hardware.power_state()
    line(OK, "Power", ("battery" if ps["on_battery"] else "plugged in") +
         (f" {ps['percent']}%" if ps["percent"] is not None else "") + (" (battery saver)" if ps["saver"] else ""))

    print("\nVoice")
    mic, why = stt.mic_available()
    line(OK if mic else BAD, "Microphone", "ready" if mic else why)
    engines = []
    for mod, name in (("sherpa_onnx", "sherpa-onnx Whisper (native ARM64/x64)"),
                      ("faster_whisper", "faster-whisper (x64)")):
        try:
            __import__(mod)
            engines.append(name)
        except Exception:
            pass
    line(OK if engines else WARN, "Speech recognition", ", ".join(engines) or "Windows offline recognizer only")
    model = stt.sherpa_files(s.stt_model)
    line(OK if model else WARN, "Whisper model", f"{s.stt_model} downloaded" if model else
         f"{s.stt_model} not downloaded yet (downloads on first use)")
    try:
        import edge_tts  # noqa: F401
        line(OK, "Voice output", "Edge neural voices + offline Windows voice")
    except Exception:
        line(WARN, "Voice output", "offline Windows voice (edge-tts not installed)")

    print("\nScreen understanding (on-device)")
    line(OK if uia.available() else WARN, "UI Automation", "ready" if uia.available() else "pip install uiautomation")
    line(OK if ocr.available() else WARN, "Windows OCR", "ready" if ocr.available() else "not installed (optional)")

    print("\nOn-device AI")
    base = s.local_url.rstrip("/")
    try:
        if s.local_engine == "ollama":
            r = requests.get(base + "/api/tags", timeout=3)
            names = [m.get("name", "") for m in r.json().get("models", [])]
            have = any(n.split(":")[0] == s.local_model.split(":")[0] for n in names)
            line(OK if have else WARN, "Ollama", f"running; {s.local_model} " + ("ready" if have else
                 f"missing - run: ollama pull {s.local_model}"))
        else:
            r = requests.get((base if base.endswith("/v1") else base + "/v1") + "/models", timeout=3)
            line(OK, "Foundry Local / server", f"running at {base}; model {s.local_model}")
    except Exception:
        line(WARN, "Local AI server", f"not reachable at {base} ({s.local_engine})")
    if hw.is_snapdragon:
        fl = hardware.detect_foundry_local(timeout=8)
        line(OK if fl["ok"] else WARN, "Foundry Local (NPU)", fl["message"])

    print("\nCloud AI (optional)")
    for p in CLOUD_PROVIDERS:
        k = get_api_key(p)
        line(OK if k else "   ", {"gemini": "Gemini", "openai": "OpenAI", "anthropic": "Anthropic"}[p],
             "key saved" if k else "no key")
    line(OK, "Privacy mode", s.mode)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
