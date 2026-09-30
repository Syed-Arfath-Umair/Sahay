"""The Sahay engine: turns (question + screen) into (answer + optional pointer),
routing between on-device and cloud AI according to the user's privacy mode.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import requests

from . import backends, privacy
from .config import Settings, get_api_key

SYSTEM_PROMPT = """You are Sahay, a friendly on-screen helper for everyday computer users.
The user is looking at their screen and asks for help. Give short, practical, step-by-step answers
in plain language (max ~120 words). If the user should click or look at a specific thing on screen,
point at it.

You get a numbered list of on-screen elements. Entries like "[Button] Search" are REAL controls
from Windows (exact positions); other entries are text read from the screen. The Windows taskbar
(Start button, Search, pinned apps) is at the BOTTOM of the screen.

Reply ONLY with a JSON object, no markdown fences:
{"answer": "<your answer>", "point": {"id": <element number or null>, "target": "<exact element name>", "x": <number or null>, "y": <number or null>, "label": "<2-4 words>"} }
- ALWAYS point with "id" when the thing to click is in the list (e.g. the taskbar "[Button] Search" or "[Button] Start").
- Point at the FIRST thing the user should click.
- Only if it is not in the list, give x,y {coord_rule}
- Use "point": null when nothing on screen needs pointing at.
"""

COORD_PIXELS = "in pixels of the screenshot image ({w}x{h}, origin top-left)."
COORD_NORMALIZED = "normalized 0-1000 (x: left→right, y: top→bottom) relative to the screenshot."

VOICE_RULES = """
Your answer will be SPOKEN ALOUD by a voice. Keep "answer" to 1-3 short conversational sentences,
no lists, no markdown, no symbols, no URLs. Say "I've pointed at it" when you point at something.
"""


@dataclass
class Pointer:
    x: int  # absolute screen coords (physical pixels), centre of the target
    y: int
    label: str = ""
    w: int = 0  # target size in physical pixels (0 = unknown -> small default box)
    h: int = 0


@dataclass
class Result:
    answer: str
    route: str                   # human-readable backend name
    is_local: bool
    pointer: Pointer | None = None
    sensitivity: int = 0
    redactions: int = 0
    screenshot_sent: bool = False
    notes: list = field(default_factory=list)
    seconds: float = 0.0
    fell_back: bool = False


def parse_reply(text: str) -> dict:
    """Robustly extract the JSON the model was asked for. Falls back to plain text."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "answer" in data:
                return data
        except json.JSONDecodeError:
            pass
    return {"answer": text.strip(), "point": None}


def _clean_name(text: str) -> str:
    return re.sub(r"^\[[^\]]*\]\s*", "", text).strip().lower()


def resolve_pointer(point, shot, lines, normalized: bool = False) -> Pointer | None:
    if not point or shot is None or not isinstance(point, dict):
        return None
    label = str(point.get("label") or "")[:40]
    lines = lines or []

    def at(ln):
        cx, cy = ln.center
        return Pointer(shot.left + cx, shot.top + cy, label or _clean_name(ln.text)[:30].title(), ln.w, ln.h)

    # 1) element id (most reliable)
    el_id = point.get("id", point.get("line"))
    if el_id is not None:
        for ln in lines:
            if str(ln.id) == str(el_id).lstrip("#"):
                return at(ln)
    # 2) element name match (controls before plain text)
    for key in (point.get("target"), label):
        name = str(key or "").strip().lower()
        if len(name) < 2:
            continue
        ranked = sorted(lines, key=lambda l: (not l.text.startswith("["), len(l.text)))
        for ln in ranked:
            if _clean_name(ln.text) == name:
                return at(ln)
        for ln in ranked:
            if name in _clean_name(ln.text):
                return at(ln)
    # 3) raw coordinates
    x, y = point.get("x"), point.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if normalized:
            fx, fy = x / 1000 * shot.width, y / 1000 * shot.height
        else:
            fx, fy = x * shot.scale, y * shot.scale
        if 0 <= fx <= shot.width and 0 <= fy <= shot.height:
            return Pointer(int(shot.left + fx), int(shot.top + fy), label)
    return None


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ---------------------------------------------------------------- routing
    def _cloud_backend(self):
        key = get_api_key(self.settings.cloud_provider)
        return backends.make_cloud(self.settings, key) if key else None

    def ask(self, question: str, shot=None, window_title: str = "", lines=None, voice: bool = False,
            force_local: bool = False) -> Result:
        from .ocr import lines_to_prompt  # local import keeps tests light

        s = self.settings
        t0 = time.time()
        lines = lines or []
        notes: list[str] = []

        q_red = privacy.redact(question)
        ocr_raw = "\n".join(ln.text for ln in lines)
        screen_red = privacy.redact(ocr_raw) if ocr_raw else privacy.RedactionResult("")
        combined = privacy.RedactionResult("", {k: q_red.found.get(k, 0) + screen_red.found.get(k, 0)
                                                for k in set(q_red.found) | set(screen_red.found)})
        decision = privacy.assess(question, window_title, combined, s.sensitivity_threshold)

        # ----- decide route
        use_cloud = False
        cloud = None
        if s.mode in ("cloud", "auto") and not force_local:
            cloud = self._cloud_backend()
            if cloud is None:
                notes.append(f"No {s.cloud_provider} API key saved - using on-device AI.")
            elif s.mode == "cloud":
                use_cloud = True
            elif decision.allow_cloud:
                use_cloud = True
            else:
                notes.append("Kept on-device: " + "; ".join(decision.reasons))

        if use_cloud:
            backend = cloud
            redact_on = s.redact_before_cloud
            send_img = s.send_screenshot and shot is not None and decision.allow_screenshot_to_cloud
            if s.send_screenshot and shot is not None and not send_img:
                notes.append("Screenshot withheld (sensitive screen); sent redacted screen text instead.")
        else:
            backend = backends.make_local(s)
            redact_on = False  # nothing leaves the PC
            send_img = s.send_screenshot and shot is not None and backend.supports_vision

        # ----- build prompt
        user_q = q_red.text if redact_on else question
        n_red = q_red.count if redact_on else 0
        parts = [f"Active window: {privacy.redact(window_title).text if redact_on else window_title or 'unknown'}"]
        if lines:
            txt, n = lines_to_prompt(lines, privacy.redact if redact_on else None, shot.scale if shot else 1.0)
            n_red += n
            parts.append("On-screen elements ('#id (x,y) name'):\n" + txt)
        elif shot is not None and not send_img:
            notes.append("No screen text available (Windows OCR not installed) - answering from the question only.")
        parts.append(f"User question: {user_q}")
        w, h = ((round(shot.width / shot.scale), round(shot.height / shot.scale)) if shot else (0, 0))
        normalized = isinstance(backend, backends.GeminiBackend)
        system = SYSTEM_PROMPT.replace("{coord_rule}", COORD_NORMALIZED if normalized else COORD_PIXELS)
        system = system.replace("{w}", str(w)).replace("{h}", str(h))
        if voice:
            system += VOICE_RULES

        # ----- call, with graceful fallback cloud -> local
        try:
            raw = backend.chat(system, "\n\n".join(parts), shot.png if send_img else None)
        except (backends.BackendError, requests.RequestException) as e:
            if not backend.is_local:
                cloud_msg = backends.friendly(e)
                notes.append(f"Cloud failed: {cloud_msg} Answered on-device instead.")
                try:
                    r = self.ask(question, shot, window_title, lines, voice, force_local=True)
                except Exception as e2:
                    raise backends.BackendError(f"Cloud: {cloud_msg}  On-device: {backends.friendly(e2)}") from e2
                r.notes = notes + r.notes
                r.seconds = time.time() - t0
                r.fell_back = True
                return r
            if isinstance(e, requests.RequestException):
                raise backends.BackendError(f"Local AI error: {e}") from e
            raise

        data = parse_reply(raw)
        return Result(
            answer=str(data.get("answer", "")).strip() or raw.strip(),
            route=backend.name, is_local=backend.is_local,
            pointer=resolve_pointer(data.get("point"), shot, lines, normalized) if s.show_pointer else None,
            sensitivity=decision.sensitivity, redactions=n_red, screenshot_sent=bool(send_img),
            notes=notes, seconds=time.time() - t0,
        )
