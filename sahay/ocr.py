"""On-device OCR using the Windows built-in OCR engine (Windows.Media.Ocr).

Runs entirely on the PC (no network). Used for:
 * giving text-only local models (e.g. NPU models via Foundry Local) "eyes",
 * precise pointing: the model names a line id, we know its exact pixel box,
 * privacy: in sensitive contexts we send *redacted OCR text* to the cloud
   instead of the raw screenshot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from PIL import Image


@dataclass
class OcrLine:
    id: int
    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


_engine = None
_available: bool | None = None


def available() -> bool:
    global _available, _engine
    if _available is None:
        try:
            from winrt.windows.media.ocr import OcrEngine  # type: ignore

            _engine = OcrEngine.try_create_from_user_profile_languages()
            _available = _engine is not None
        except Exception:
            _available = False
    return _available


async def _recognize(img: Image.Image):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap  # type: ignore
    from winrt.windows.storage.streams import DataWriter  # type: ignore

    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                 rgba.width, rgba.height)
    return await _engine.recognize_async(bmp)


def recognize(img: Image.Image, max_lines: int = 250) -> list[OcrLine]:
    if not available():
        return []
    # Windows OCR has a max image dimension; downscale if needed and map back.
    limit = 4000
    scale = 1.0
    if max(img.size) > limit:
        scale = max(img.size) / limit
        img = img.resize((int(img.width / scale), int(img.height / scale)))
    try:
        result = asyncio.run(_recognize(img))
    except Exception:
        return []
    lines: list[OcrLine] = []
    for i, line in enumerate(result.lines):
        if i >= max_lines:
            break
        rects = [w.bounding_rect for w in line.words]
        if not rects:
            continue
        x0 = min(r.x for r in rects)
        y0 = min(r.y for r in rects)
        x1 = max(r.x + r.width for r in rects)
        y1 = max(r.y + r.height for r in rects)
        lines.append(OcrLine(len(lines) + 1, line.text,
                             int(x0 * scale), int(y0 * scale), int((x1 - x0) * scale), int((y1 - y0) * scale)))
    return lines


def lines_to_prompt(lines: list[OcrLine], redactor=None, scale: float = 1.0) -> tuple[str, int]:
    """Format OCR lines as '#id (x,y) text' with coords in the downscaled screenshot space.
    Returns (text, redaction_count)."""
    out, n = [], 0
    for ln in lines:
        t = ln.text
        if redactor:
            rr = redactor(t)
            t, n = rr.text, n + rr.count
        cx, cy = ln.center
        out.append(f"#{ln.id} ({round(cx / scale)},{round(cy / scale)}) {t}")
    return "\n".join(out), n
