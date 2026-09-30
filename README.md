# Sahay: a private, voice-first AI helper for Snapdragon PCs

> **Sahay** (सहाय, Hindi for *help*) lives quietly in your system tray. Press one shortcut, **ask out loud** about anything on your screen, and Sahay **answers in a natural voice** while a glowing cursor **flies to the exact button you need and draws a box around it**. A privacy layer decides, **on your PC**, whether each question may use the cloud or must stay on-device.

Built for the **Snapdragon® AI Lab Build & Present Challenge**. Sahay is designed and optimised for **Snapdragon-powered HP PCs** (Windows on ARM64, Hexagon NPU).

---

## Contents
1. [What it does](#what-it-does)
2. [Highlights](#highlights)
3. [Built for Snapdragon](#built-for-snapdragon)
4. [The privacy layer](#the-privacy-layer)
5. [How it works](#how-it-works)
6. [Install](#install)
7. [Using Sahay](#using-sahay)
8. [Choosing your AI](#choosing-your-ai)
9. [Troubleshooting](#troubleshooting)
10. [Project structure and tests](#project-structure-and-tests)
11. [Models, credits and licence](#models-credits-and-licence)

---

## What it does

| You do | Sahay does |
|---|---|
| Press **Ctrl + Alt + Space** anywhere | A glowing cursor buddy appears beside your mouse with a translucent **Listening** pill. Voice bars react to your voice. |
| Ask *"How do I open Notepad?"* | On-device Whisper transcribes you. The pill turns to **Analyzing** with a shimmer and an orbiting spinner, and the buddy keeps following your mouse. |
| Wait a second | Sahay **speaks the answer** (**Answering**). The buddy **flies along a curve to the taskbar Search button**, and a **glowing box draws itself around it**. |
| Stay quiet for 5 seconds | A sleek glass **text box** appears instead. Type, press Enter, and the answer is still spoken. |
| Press **Esc** (or the shortcut again) | The question is cancelled instantly, including listening, thinking or speaking. |
| Click the **tray icon** | A minimal window with your **chat history** (text answers, where each ran, what was hidden) and **Settings**. |

It helps people who find computers confusing, such as first-time users, parents and grandparents, anyone stuck on a settings page, error dialog, government portal or spreadsheet. Nothing needs to be copied and pasted, and there's no need to translate an answer back into clicks.

## Highlights

- **Voice-first and zero-effort.** One shortcut, speak, hear the answer, see where to click.
- **Points at real controls, not guesses.** Sahay reads the actual buttons, menus, tabs and taskbar items through **Windows UI Automation** (plus on-device OCR). The AI picks an element by number and Sahay knows its exact position and size. That's where the pixel-accurate pointer and highlight box come from.
- **Private by design.**
  - Three modes: 🔒 On-device, ⚖ Auto and ☁ Cloud.
  - Personal details are removed before any cloud call: Aadhaar, PAN, UPI, IFSC, +91 phone numbers, emails, card numbers, passwords/OTP and API keys.
  - Sensitive screens (banking, password managers, mail, health) stay on the PC in Auto mode.
- **Bring your own cloud key** (Gemini, OpenAI or Anthropic).
  - Keys are encrypted with your Windows account (Credential Manager + DPAPI).
  - Model names retire often, so Sahay **auto-discovers a working model**.
  - If the cloud fails, it **falls back to on-device AI** automatically.
- **Smart voice privacy.** Answers produced on-device are read by the **offline Windows voice**, so a private answer is never sent anywhere just to be spoken. Cloud answers use natural Edge neural voices, including Indian English and Hindi.
- **Polished, hand-painted UI.**
  - 60 fps animations with frame-rate-independent easing.
  - Click-through, always-on-top overlays that are **hidden from screen capture**, so Sahay never sees itself.
  - A dark, minimal tray window with grouped, auto-saving settings.
- **Cancel anywhere:** Esc, the shortcut again, the tray menu, or **Stop** in the chat window.

## Built for Snapdragon

Sahay is designed first for **Windows on ARM64** and the **Snapdragon X-series Hexagon NPU** found in Snapdragon-powered HP PCs (e.g. HP OmniBook with Snapdragon X / X2).

| Area | How Sahay uses Snapdragon |
|---|---|
| **Native ARM64, no emulation** | Every dependency was chosen because it ships **native `win_arm64` wheels**: PySide6 (Qt UI), NumPy, sounddevice, **sherpa-onnx** (speech), **onnxruntime-qnn** (NPU), Pillow; the rest is pure Python. `setup.ps1` detects ARM64, **finds a native ARM64 Python** (`py -V:3.12-arm64`) and warns if only an emulated x64 Python exists. The in-app *This PC* panel and `doctor.bat` show whether Sahay runs natively. |
| **Hexagon NPU language models** | *Settings → On-device AI → Use Snapdragon NPU* auto-detects **Microsoft Foundry Local**, which serves **QNN-compiled models on the Hexagon NPU** (e.g. Phi-4-mini NPU builds). It picks the NPU model and switches Sahay to it. Sahay feeds text-only NPU models the on-device **UI element list + OCR text** instead of a screenshot, so they can still point precisely. |
| **Qualcomm QNN runtime** | `hardware.py` detects `QNNExecutionProvider` (onnxruntime-qnn) and configures the **HTP backend in burst mode**. `tools/npu_benchmark.py` runs any **Qualcomm AI Hub** ONNX model on CPU vs NPU and reports the speed-up. |
| **On-device speech** | Whisper runs through **sherpa-onnx** (ONNX Runtime inside) with **int8** weights on the Oryon CPU cores. It's native ARM64, with no CTranslate2/x64 dependency. |
| **Latency tricks** | While you're still speaking, Sahay (1) reads the screen's controls and text and (2) **pre-loads the on-device model into memory** (speculative warm-up, 30-minute keep-alive). The answer starts as soon as you stop talking. |
| **All-day battery** | Idle in the tray there are **no timers or polling**; only the hotkey hook runs. Sahay marks itself **EcoQoS** (efficiency cores) when idle and switches to high QoS while answering. With Windows **battery saver** on, animations drop from 60 to 30 fps. Heavy AI work belongs on the NPU (Foundry Local) rather than the GPU or CPU. |
| **OS-native on-device services** | Screen understanding uses Windows **UI Automation** and the built-in **Windows OCR engine**. Voice output uses the built-in Windows voice, so these add no extra model download. |
| **HiDPI-correct** | DPI-aware coordinate mapping keeps the pointer and highlight box exact on high-resolution HP panels and multi-monitor setups. |

> Sahay also runs unchanged on x64 Intel/AMD Windows PCs. It uses DirectML/CPU there instead of QNN, so developers can build and test without a Snapdragon device.

## The privacy layer

Everything below runs **on the PC**, before any network call.

| Mode | Behaviour |
|---|---|
| 🔒 **On-device** | Nothing leaves the PC: no screenshot, no text, no voice. |
| ⚖ **Auto** *(default)* | Each request gets a local sensitivity score, based on the active window (banking apps, UPI, password managers, mail, WhatsApp, health, DigiLocker, income tax…), the question, and any personal details found. Sensitive requests **stay on-device**. Everything else may use your cloud key. |
| ☁ **Cloud** | Uses your key for every request. Personal details are **still redacted**, and on sensitive screens the **raw screenshot is withheld**; only redacted on-screen text is sent. |

- **Redaction** (on-device, before cloud): Aadhaar, PAN, IFSC, UPI IDs, +91 phone numbers, emails, **Luhn-validated** card numbers, API keys, IP addresses, and passwords/PINs/OTPs next to their labels.
- **Transparency:** every answer in the chat window shows where it ran (🔒/☁ and model), how long it took, how many details were hidden, whether the screen was shared, and why it stayed local or fell back.
- **Voice privacy:** speech recognition is always on-device. In *Smart* voice mode, on-device answers use the offline Windows voice.
- **Keys:** stored in Windows Credential Manager and a DPAPI-encrypted file tied to your Windows account. They're never kept in plain text.

## How it works

```
 Ctrl+Alt+Space
      │
      ├─► capture screen + active window (before any Sahay window appears)
      ├─► [background] read the screen: UI Automation controls (+ taskbar Start/Search) + Windows OCR
      ├─► [background] warm up the on-device model (Ollama keep-alive / Foundry Local)
      │
      ├─► Listening ── mic + voice-activity detection ──► (no speech for 5 s) ──► glass text box
      │                          │
      │                          ▼
      │               on-device Whisper (sherpa-onnx int8)
      │
      ├─► Analyzing ── Privacy layer (local): redact PII · score sensitivity · decide route
      │                     │
      │          ┌──────────┴───────────┐
      │          ▼                      ▼
      │   On-device AI             Cloud AI (your key)
      │   Ollama (Qwen2.5-VL)      Gemini / OpenAI / Anthropic
      │   Foundry Local (NPU)      auto model discovery · fallback to on-device
      │          └──────────┬───────────┘
      │                     ▼
      │      JSON: {answer, point: {element id | name | x,y}}
      │
      └─► Answering ── speak (Edge neural voice / offline Windows voice)
                    ── buddy flies to the element · highlight box draws around it
```

## Install

### Requirements
- Windows 11 (Windows 10 21H2+ works). **Snapdragon X / X2 HP PC recommended.** x64 Intel/AMD also supported.
- **Python 3.11–3.13.** On Snapdragon, use the **ARM64 installer** from [python.org](https://www.python.org/downloads/windows/). Tick *Add python.exe to PATH*.
- A microphone for voice. Typing works without one.
- On-device AI, at least one of:
  - **Ollama** ([download](https://ollama.com/download), native ARM64 build). Setup pulls `qwen2.5vl:3b`, about 3 GB.
  - **Microsoft Foundry Local** for the Snapdragon NPU: `winget install Microsoft.FoundryLocal`, then `foundry model run phi-4-mini`.
- Optional: a cloud API key. A free Gemini key from [Google AI Studio](https://aistudio.google.com/apikey) works well.

### Steps
```powershell
# 1. unzip Sahay, open PowerShell in the folder, then:
powershell -ExecutionPolicy Bypass -File setup.ps1

# 2. start it (runs in the system tray, no window)
run.bat
```
`setup.ps1` does the following:
1. Detects Snapdragon and picks a **native ARM64 Python**.
2. Creates `.venv` and installs the packages.
3. Downloads the on-device Whisper model once (~100 MB).
4. Pulls the Ollama model and prints NPU (Foundry Local) instructions.
5. Runs the **health check**.

## Using Sahay

| Action | How |
|---|---|
| Ask by voice | **Ctrl + Alt + Space**, then speak. Sahay stops listening when you pause. |
| Finish talking early | Press the shortcut again. |
| Type instead | Stay quiet for 5 seconds (adjustable 3–10 s) and the text box appears. |
| Cancel | **Esc**, the shortcut again, tray → *Cancel*, or **■ Stop** in the chat window. |
| History and text answers | Click the tray icon → **Chat**. You can also type questions there. |
| Settings | Tray icon → **Settings**. Everything saves automatically. |

**Settings overview**
- **Privacy:** mode (On-device / Auto / Cloud), screen access, redaction, Auto-mode strictness.
- **Voice:**
  - Speak-to-ask, and how long to wait for speech.
  - Speech model: *Fast* (tiny) or *Accurate* (base), with live status.
  - Read answers aloud, voice choice (Indian English, Hindi, US, UK), and engine (Smart / Edge / Offline).
- **Cloud AI:**
  - Provider and API key (with show/hide).
  - Model: leave it on *Automatic*.
  - **Test cloud** shows exactly which model answered, or why it failed.
- **On-device AI:**
  - **Use Snapdragon NPU** auto-detects Foundry Local.
  - Engine, server, model, and whether the model can see images.
  - **Test on-device AI**.
- **Pointing:** fly the buddy to targets, draw the highlight box.
- **General:** shortcut, battery-saver animations.
- **This PC:** processor, native ARM64 status, AI runtime (NPU/GPU/CPU), and screen-reading engines.

## Choosing your AI

| Setup | Good for | How |
|---|---|---|
| **Snapdragon NPU** (Foundry Local) | Best battery life, fully private | Install Foundry Local, then run `foundry model run phi-4-mini`. In Sahay: Settings → *Use Snapdragon NPU*. |
| **Ollama vision model** | Fully private, and the model sees the screenshot | `ollama pull qwen2.5vl:3b` (the default). |
| **Faster on CPU-only PCs** | Older/x64 machines | `ollama pull qwen2.5:3b`, set it as the model, and turn off *Model can see images*. |
| **Cloud** (Gemini/OpenAI/Anthropic) | Best answer quality | Paste a key in Settings → *Test cloud*. Choose **Auto** mode to keep private screens local. |

## Troubleshooting

| Problem | Fix |
|---|---|
| Anything unclear | Run **`doctor.bat`**. It checks native ARM64, NPU runtime, microphone, speech model, screen reading, local AI and cloud keys. |
| Need to see errors | Run **`run_debug.bat`** (keeps a console open). Logs from `run.bat` go to `%APPDATA%\Sahay\sahay.log`. |
| Cloud answers come from on-device | Open the chat window: the note under the answer says why (bad key, quota, no internet). Use **Test cloud** in Settings. Remember that **Auto** keeps sensitive screens local by design. |
| "On-device AI isn't running" | Start Ollama (or Foundry Local), then use **Test on-device AI**. |
| Pointer misses | Make sure `uiautomation` is installed (`doctor.bat`). Sahay points best at real controls. |
| Setup says only x64 Python found (Snapdragon) | Install the **ARM64** Python 3.12 build and re-run `setup.ps1`. It rebuilds `.venv` for ARM64. |
| Voice not heard | Check Windows microphone privacy settings. Sahay switches to the text box automatically if no speech is detected. |

## Project structure and tests

```
sahay/
  app.py            tray, global hotkey + Esc, session state machine, cancel
  engine.py         prompt building, routing, cloud→local fallback, pointer resolution
  privacy.py        on-device PII redaction + sensitivity scoring
  backends.py       Ollama, Foundry Local/OpenAI-compatible, Gemini, OpenAI, Anthropic (plain HTTP)
                    + retired-model fallback and model discovery
  uia.py            Windows UI Automation: real controls with exact bounds (incl. taskbar)
  ocr.py            Windows on-device OCR
  capture.py        screenshot of the active monitor, window title, cursor (DPI-aware)
  hardware.py       Snapdragon / native-ARM64 / QNN detection, power state, EcoQoS, Foundry Local
  config.py         settings (+ upgrades), encrypted API keys (Credential Manager + DPAPI)
  doctor.py         readiness checklist (python -m sahay.doctor)
  voice/stt.py      mic + VAD, sherpa-onnx Whisper (native ARM64), fallbacks
  voice/tts.py      Edge neural voices, offline Windows voice, privacy-aware choice
  ui/overlay.py     60 fps cursor buddy + glass pill (Listening / Analyzing / Answering)
  ui/highlight.py   self-drawing glowing box around the target
  ui/typebox.py     glass text box
  ui/window.py      tray window: chat + settings
tools/npu_benchmark.py   CPU vs Hexagon NPU timing for Qualcomm AI Hub ONNX models
tests/                   30 unit tests
```

Run the tests: `.venv\Scripts\python.exe tests\run_tests.py`. They cover redaction, sensitivity scoring, every routing path, cloud model fallback/discovery, error handling, voice-activity detection, pointing and settings upgrades.

## Models, credits and licence

Sahay adds **open-source AI models** on top of OS-native services:
- **Whisper** (OpenAI) speech recognition, run through **sherpa-onnx** (k2-fsa) with int8 ONNX weights.
- **Qwen2.5-VL** vision-language model via **Ollama**, or **NPU-optimised models via Microsoft Foundry Local** on Snapdragon.
- **Qualcomm AI Hub** ONNX models can be benchmarked on the NPU with `tools/npu_benchmark.py`.
- Optional cloud models through the user's own API key.

Each model and library is used under its own licence. Sahay's own code is **MIT-licensed** (see `LICENSE`).

## Roadmap
- Run Whisper's encoder on the Hexagon NPU (Qualcomm AI Hub Whisper + onnxruntime-qnn).
- Multi-step guidance: point → wait for the click → point at the next step.
- Hindi and other Indian-language voice input.
- MSIX installer for the Microsoft Store / HP preinstall.
