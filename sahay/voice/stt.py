"""Voice input, 100 % on-device.

* Microphone capture + energy-based voice-activity detection (VAD).
* Speech-to-text engines, tried in order:
    1. sherpa-onnx Whisper (int8, ONNX Runtime inside) - native Windows ARM64 + x64 wheels,
       so it runs natively on Snapdragon.  <- primary
    2. faster-whisper (CTranslate2) - x64 only (no Windows ARM64 wheels), used if present.
    3. Windows offline speech recognizer (System.Speech) - built into Windows, last resort.

The Whisper model is downloaded once (~200 MB for base.en, ~120 MB for tiny.en) to
%LOCALAPPDATA%\\Sahay\\models. After that, nothing touches the network.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

from ..config import DATA_DIR

SR = 16000
BLOCK = 480  # 30 ms
MODELS_DIR = DATA_DIR / "models"
SHERPA_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-{name}.tar.bz2"


def mic_available() -> tuple[bool, str]:
    try:
        import sounddevice as sd

        sd.query_devices(kind="input")
        return True, ""
    except Exception as e:
        return False, f"no microphone ({e.__class__.__name__})"


def available() -> tuple[bool, str]:
    ok, why = mic_available()
    if not ok:
        return False, why
    for mod in ("sherpa_onnx", "faster_whisper"):
        try:
            __import__(mod)
            return True, ""
        except Exception:
            pass
    if platform.system() == "Windows":
        return True, ""  # System.Speech fallback
    return False, "no speech engine installed"


# ------------------------------------------------------------------ VAD
class VAD:
    """Energy-based voice activity detector. Pure numpy so it's unit-testable."""

    def __init__(self, no_speech_timeout=5.0, end_silence=1.2, max_seconds=25.0, calib_seconds=0.25):
        self.no_speech_timeout, self.end_silence = no_speech_timeout, end_silence
        self.max_seconds, self.calib_seconds = max_seconds, calib_seconds
        self.calib: list[float] = []
        self.speech_started = False
        self.loud_run = 0
        self.last_voice = 0.0

    def threshold(self) -> float:
        floor = float(np.median(self.calib)) if self.calib else 0.004
        return max(min(floor, 0.03) * 3.0, 0.012)

    def feed(self, block: np.ndarray, t: float) -> tuple[str, float]:
        """Returns (status, level 0..1). status: 'wait' | 'no_speech' | 'done'."""
        rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
        if t < self.calib_seconds:
            self.calib.append(rms)
            return "wait", 0.0
        thr = self.threshold()
        level = min(1.0, rms / (thr * 5))
        if rms > thr:
            self.loud_run += 1
            if self.loud_run >= 3:  # ~90 ms of sound = speech, ignores clicks
                self.speech_started = True
                self.last_voice = t
        else:
            self.loud_run = 0
        if not self.speech_started and t > self.no_speech_timeout:
            return "no_speech", level
        if self.speech_started and t - self.last_voice > self.end_silence:
            return "done", level
        if t > self.max_seconds:
            return "done", level
        return "wait", level


def record(stop: threading.Event, on_level, no_speech_timeout: float = 5.0) -> np.ndarray | None:
    """Record until the user stops talking. Returns None if they never spoke.
    If `stop` is set (hotkey pressed again): returns audio so far if speech started, else None."""
    import sounddevice as sd

    vad = VAD(no_speech_timeout=no_speech_timeout)
    frames: list[np.ndarray] = []
    start = time.monotonic()
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK) as stream:
        while True:
            data, _ = stream.read(BLOCK)
            x = data[:, 0].copy()
            frames.append(x)
            status, level = vad.feed(x, time.monotonic() - start)
            on_level(level)
            if stop.is_set():
                break
            if status == "no_speech":
                return None
            if status == "done":
                break
    on_level(0.0)
    if not vad.speech_started:
        return None
    return np.concatenate(frames)


# ------------------------------------------------------------------ model files
def sherpa_model_dir(name: str) -> Path:
    return MODELS_DIR / f"sherpa-onnx-whisper-{name}"


def sherpa_files(name: str) -> dict | None:
    d = sherpa_model_dir(name)
    enc, dec, tok = d / f"{name}-encoder.int8.onnx", d / f"{name}-decoder.int8.onnx", d / f"{name}-tokens.txt"
    if enc.exists() and dec.exists() and tok.exists():
        return {"encoder": str(enc), "decoder": str(dec), "tokens": str(tok)}
    return None


def ensure_sherpa_model(name: str = "base.en", progress=None) -> dict:
    """Download + extract only the int8 encoder/decoder and tokens. Returns file paths."""
    files = sherpa_files(name)
    if files:
        return files
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    url = SHERPA_URL.format(name=name)
    tmp = Path(tempfile.gettempdir()) / f"sahay-whisper-{name}.tar.bz2"

    def hook(blocks, bs, total):
        if progress and total > 0:
            progress(min(1.0, blocks * bs / total))

    urllib.request.urlretrieve(url, tmp, reporthook=hook)
    wanted = {f"{name}-encoder.int8.onnx", f"{name}-decoder.int8.onnx", f"{name}-tokens.txt"}
    target = sherpa_model_dir(name)
    partial = target.with_suffix(".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with tarfile.open(tmp, "r:bz2") as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name)
            if m.isfile() and base in wanted:
                src = tf.extractfile(m)
                with open(partial / base, "wb") as f:
                    shutil.copyfileobj(src, f)
    shutil.rmtree(target, ignore_errors=True)
    partial.rename(target)
    try:
        tmp.unlink()
    except OSError:
        pass
    files = sherpa_files(name)
    if not files:
        raise RuntimeError("Speech model download incomplete")
    return files


# ------------------------------------------------------------------ transcriber
def _write_wav(audio: np.ndarray, path: str) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


_PS_STT = r"""
Add-Type -AssemblyName System.Speech
try { $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine }
catch { $r = New-Object System.Speech.Recognition.SpeechRecognitionEngine([System.Globalization.CultureInfo]::GetCultureInfo('en-US')) }
$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$r.SetInputToWaveFile('__PATH__')
$out = @()
for ($i = 0; $i -lt 40; $i++) { $res = $r.Recognize(); if ($res -eq $null) { break }; $out += $res.Text }
[Console]::Out.Write(($out -join ' '))
"""


class Transcriber:
    def __init__(self, model_name: str = "base.en"):
        self.model_name = model_name if model_name in ("tiny.en", "base.en") else "base.en"
        self._rec = None
        self._fw = None
        self._lock = threading.Lock()
        self.engine = ""        # which engine is active
        self.status = "not loaded"
        self.error = ""
        self._attempted = False  # load once; don't retry a failed download on every question

    def set_model(self, name: str) -> None:
        """Switch model without blocking the UI (a download may be holding the lock)."""
        if name == self.model_name:
            return

        def swap():
            with self._lock:
                self.model_name = name
                self._rec = None
                self._fw = None
                self.engine = ""
                self._attempted = False
            self._load()

        threading.Thread(target=swap, daemon=True).start()

    def preload(self) -> None:
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        with self._lock:
            if self._rec is not None or self._fw is not None or self._attempted:
                return
            self._attempted = True
            # 1) sherpa-onnx (native ARM64)
            try:
                import sherpa_onnx

                files = sherpa_files(self.model_name)
                if files is None:
                    self.status = "downloading speech model… 0%"
                    files = ensure_sherpa_model(
                        self.model_name, lambda f: setattr(self, "status", f"downloading speech model… {int(f * 100)}%"))
                threads = max(2, min(4, (os.cpu_count() or 4) // 2))
                self._rec = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=files["encoder"], decoder=files["decoder"], tokens=files["tokens"],
                    language="en", task="transcribe", num_threads=threads, provider="cpu")
                self.engine = f"Whisper {self.model_name} (sherpa-onnx, int8, {threads} threads)"
                self.status = "ready"
                return
            except ImportError:
                pass
            except Exception as e:
                self.error = f"sherpa-onnx: {e}"
            # 2) faster-whisper (x64)
            try:
                from faster_whisper import WhisperModel

                self._fw = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                self.engine = f"Whisper {self.model_name} (faster-whisper, int8)"
                self.status = "ready"
                return
            except ImportError:
                pass
            except Exception as e:
                self.error += f" faster-whisper: {e}"
            # 3) Windows offline recognizer
            if platform.system() == "Windows":
                self.engine = "Windows offline speech recognizer"
                self.status = "ready (basic)"
            else:
                self.status = "unavailable"

    def transcribe(self, audio: np.ndarray) -> str:
        self._load()
        if self._rec is not None:
            stream = self._rec.create_stream()
            # a little trailing silence helps Whisper finish the last word
            stream.accept_waveform(SR, np.concatenate([audio, np.zeros(int(SR * 0.3), np.float32)]))
            self._rec.decode_stream(stream)
            return stream.result.text.strip()
        if self._fw is not None:
            segs, _ = self._fw.transcribe(audio, beam_size=1, language="en", condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segs).strip()
        if platform.system() == "Windows":
            path = os.path.join(tempfile.gettempdir(), f"sahay_{os.getpid()}_{int(time.time() * 1000)}.wav")
            _write_wav(audio, path)
            try:
                p = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_STT.replace("__PATH__", path)],
                                   capture_output=True, text=True, timeout=30,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return (p.stdout or "").strip()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise RuntimeError(f"No speech engine available. {self.error}")
