"""Voice output.

* Edge neural voices (edge-tts) - natural sounding; the answer text goes to Microsoft's TTS service.
* Offline Windows voice (System.Speech) - nothing leaves the PC.

Privacy rule (tts_engine = "auto"): answers produced on-device are spoken with the offline voice,
so a sensitive, locally-kept answer is never sent anywhere just to be read aloud.

MP3 playback uses the Windows MCI API (winmm) - zero extra dependencies, works on ARM64.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import subprocess
import tempfile
import threading
import time
import uuid

VOICES = [
    ("en-IN-NeerjaNeural", "Neerja - English (India)"),
    ("en-IN-PrabhatNeural", "Prabhat - English (India)"),
    ("en-US-AriaNeural", "Aria - English (US)"),
    ("en-US-GuyNeural", "Guy - English (US)"),
    ("en-GB-SoniaNeural", "Sonia - English (UK)"),
    ("hi-IN-SwaraNeural", "Swara - Hindi"),
    ("hi-IN-MadhurNeural", "Madhur - Hindi"),
]


def clean_for_speech(text: str) -> str:
    t = re.sub(r"\[([A-Z_]+?)_\d+\]", r"\1", text)  # redaction tokens read naturally
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def _mci(cmd: str) -> tuple[int, str]:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    return err, buf.value


class Speaker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def speak(self, text: str, online: bool, voice: str, rate: str, on_done=None) -> None:
        self.stop()
        self._stop = threading.Event()
        text = clean_for_speech(text)
        self._thread = threading.Thread(target=self._run, args=(text, online, voice, rate, on_done, self._stop),
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------ internals
    def _run(self, text, online, voice, rate, on_done, stop):
        try:
            played = False
            if online and text:
                try:
                    played = self._edge(text, voice, rate, stop)
                except Exception as e:
                    print("Edge TTS failed, using offline voice:", e)
            if not played and text and not stop.is_set():
                self._offline(text, rate, stop)
        finally:
            if on_done:
                on_done()

    def _edge(self, text, voice, rate, stop) -> bool:
        import edge_tts

        path = os.path.join(tempfile.gettempdir(), f"sahay_{uuid.uuid4().hex}.mp3")
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(path))
        if stop.is_set():
            return True
        try:
            return self._play_mp3(path, stop)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _play_mp3(path: str, stop: threading.Event) -> bool:
        if platform.system() != "Windows":
            return False
        alias = "sahay" + uuid.uuid4().hex[:8]
        err, _ = _mci(f'open "{path}" type mpegvideo alias {alias}')
        if err:
            return False
        try:
            _mci(f"play {alias}")
            t0 = time.monotonic()
            while not stop.is_set():
                _, mode = _mci(f"status {alias} mode")
                if mode != "playing" and time.monotonic() - t0 > 0.4:
                    break
                time.sleep(0.05)
            _mci(f"stop {alias}")
        finally:
            _mci(f"close {alias}")
        return True

    def _offline(self, text, rate, stop):
        if platform.system() != "Windows":
            return
        try:
            pct = int(rate.strip().rstrip("%"))
        except ValueError:
            pct = 0
        sapi_rate = max(-10, min(10, round(pct / 10)))
        safe = text.replace("'", "''")[:3000]
        ps = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Rate = {sapi_rate}; $s.Speak('{safe}')")
        self._proc = subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while self._proc.poll() is None:
            if stop.is_set():
                self._proc.terminate()
                break
            time.sleep(0.05)
