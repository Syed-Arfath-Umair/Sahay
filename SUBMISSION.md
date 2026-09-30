# Sahay: submission pack (Snapdragon® AI Lab Build & Present Challenge)

Use these texts in the submission form. Each section is ready to paste.

---

## Project name
**Sahay: a private, voice-first AI helper for Snapdragon PCs**

## One-line summary
Press one key, ask out loud about anything on your screen, and Sahay answers in a natural voice while a cursor flies to the exact button and highlights it. An on-device privacy layer decides whether each question may use the cloud. It's built natively for Snapdragon Windows on ARM and the Hexagon NPU.

## Short description (~150 words)
Many people struggle with everyday computer tasks: settings pages, error dialogs, government and banking portals, spreadsheets. AI assistants could help, but they demand effort (copy, paste, translate the answer into clicks) and they upload the whole screen to the cloud.

Sahay removes both barriers. It lives in the system tray. Press Ctrl+Alt+Space and speak. On-device Whisper transcribes you, and Sahay reads the real on-screen controls through Windows UI Automation. Its answer is spoken aloud while an animated cursor flies to the exact control and draws a glowing box around it.

A local privacy layer scores every request. Sensitive screens (banking, UPI, mail, health) stay on-device, and Indian personal data (Aadhaar, PAN, UPI, phone numbers) is redacted before anything reaches the cloud. Users choose On-device, Auto or Cloud, using their own Gemini/OpenAI/Anthropic key. Sahay runs natively on Snapdragon (ARM64) and can run its language model on the Hexagon NPU through Foundry Local.

## Problem
1. **Effort:** users must copy text into a chat, then translate the answer back into clicks.
2. **Privacy:** screen-aware assistants send the entire screen, including balances, chats and ID numbers, to the cloud.
3. **Access:** first-time and elderly users need voice, plain language and "show me where", not a wall of text.

## Solution and key features
- **One shortcut → speak → hear the answer → see where to click.** If the user stays silent for 5 s, a text box appears. Esc cancels at any time.
- **Pointing at real controls:** Windows UI Automation + OCR give the AI a numbered list of real buttons (including taskbar Start/Search), so the pointer and the self-drawing highlight box land exactly on target.
- **Privacy layer (on-device):**
  - Three modes, per-request sensitivity scoring, and PII redaction.
  - The screenshot is withheld on sensitive screens.
  - Each answer shows a transparency note: where it ran, what was hidden, and why.
- **Hybrid AI:**
  - On-device: Ollama Qwen2.5-VL, or Foundry Local NPU models.
  - Cloud: the user's own key, with automatic model discovery and fallback to on-device.
- **Privacy-aware voice:** on-device answers are spoken with the offline Windows voice; cloud answers use Edge neural voices (Indian English and Hindi available).

## How it is optimised for Snapdragon-powered HP PCs
- **Native Windows on ARM64:** every dependency was verified to ship `win_arm64` wheels (PySide6, NumPy, sounddevice, sherpa-onnx, onnxruntime-qnn) or to be pure Python. Setup finds a native ARM64 Python and warns about x64 emulation. The in-app *This PC* panel and `doctor.bat` show the native status.
- **Hexagon NPU:**
  - One-click **"Use Snapdragon NPU"** detects Microsoft Foundry Local and switches Sahay to a QNN/NPU language model.
  - Text-only NPU models get the on-device UI element list instead of an image.
  - `onnxruntime-qnn` is detected and configured (HTP backend, burst mode), and `tools/npu_benchmark.py` measures CPU vs NPU speed-up for Qualcomm AI Hub models.
- **Efficient on-device speech:** Whisper int8 via sherpa-onnx, running natively on the Oryon CPU.
- **Low latency:** screen reading and model warm-up (keep-alive) happen *while the user is still speaking*.
- **Battery:**
  - No idle timers or polling in the tray.
  - EcoQoS (efficiency cores) when idle, high QoS while answering.
  - 30 fps animations on battery saver.
- **HiDPI-correct** pointing on high-resolution HP displays and multi-monitor setups.

## Evaluation criteria mapping

### Technical implementation
- Modular Python/Qt app:
  - privacy engine, router and 5 AI backends (plain HTTP, no heavy SDKs);
  - UI Automation + OCR screen understanding;
  - VAD + on-device Whisper, privacy-aware TTS;
  - 60 fps hand-painted overlays, click-through and excluded from screen capture.
- Robust cloud layer: error classification (bad key, quota, retired model), model fallback chain plus live model discovery, and graceful on-device fallback.
- 30 automated unit tests: redaction, sensitivity, routing, fallback/discovery, errors, VAD, pointing, settings upgrades.

### Application use case and innovation
- **Privacy-aware hybrid routing** decided per request on the device, instead of all-local or all-cloud.
- **India-first PII protection** (Aadhaar, PAN, UPI, IFSC, +91 numbers).
- **Show, don't tell:** a voice answer plus a pointer and highlight on the real control.
- Accessibility for first-time and elderly users (voice in and out, no typing required).

### Deployment and accessibility
- `setup.ps1` does a one-command install. It detects Snapdragon, installs native ARM64 packages, downloads the speech model, pulls the local model and runs a health check.
- `run.bat` starts Sahay in the tray.
- `doctor.bat` gives a readiness checklist, and `run_debug.bat` keeps a console open for logs.
- Works fully offline (on-device mode). Cloud is optional and uses the user's own key.
- Also runs on x64 PCs (DirectML/CPU), so anyone can contribute.

### Presentation and documentation
- Detailed README: features, Snapdragon optimisations, privacy model, architecture diagram, install, usage, troubleshooting.
- This submission pack, with a demo script.

## Open-source AI models used
- Whisper (speech recognition) via sherpa-onnx, int8 ONNX.
- Qwen2.5-VL (vision-language) via Ollama.
- NPU-optimised models (e.g. Phi-4-mini) via Microsoft Foundry Local on Snapdragon.
- Qualcomm AI Hub ONNX models via the NPU benchmark tool.

---

## Demo video script (about 2½ minutes)

1. **Intro (15 s):** "This is Sahay, a private, voice-first AI helper built for Snapdragon PCs. It lives in the tray." Show the tray icon.
2. **Voice + pointing (35 s):**
   - Press Ctrl+Alt+Space and say *"How do I open Notepad?"*
   - Show the pill going Listening → Analyzing → Answering.
   - The voice answers while the buddy flies to taskbar **Search** and the box draws around it.
3. **Real app (25 s):** open Excel, then hotkey → *"How do I freeze the top row?"* The pointer and highlight land on **View**.
4. **Typing + cancel (15 s):**
   - Press the hotkey and stay silent; the glass text box appears. Type a question.
   - Press the hotkey again, then **Esc**: "Cancelled".
5. **Privacy (30 s):**
   - Open a (mock) bank page and ask *"What is this charge?"*
   - Open the tray window: the answer is marked 🔒 On-device, with the note "Kept on-device: active window looks sensitive".
   - Type a question containing a phone number and PAN in Cloud mode; the note shows "2 details hidden".
6. **Snapdragon (25 s):**
   - Settings → *This PC* shows the Snapdragon processor, "Native ARM64" and the NPU runtime.
   - Click **Use Snapdragon NPU** (Foundry Local), then ask again; the answer runs on the NPU.
   - Optionally run `tools\npu_benchmark.py` with an AI Hub model to show the CPU vs NPU speed-up.
7. **Close (5 s):** "Sahay: help that's one keypress away, and private by design."
