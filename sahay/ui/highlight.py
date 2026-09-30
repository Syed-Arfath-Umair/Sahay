"""Animated highlight box drawn around the on-screen element Sahay is pointing at.

A click-through, capture-excluded, always-on-top window hugging the target. The border
'draws itself' around the element, then breathes with a soft glow until dismissed.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import native

PAD = 14          # room for the glow outside the target
MIN_W, MIN_H = 44, 30


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Highlight(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._clock = QElapsedTimer()
        self.color = QColor(61, 214, 140)
        self.fade_start: float | None = None
        self.label = ""
        self._token = 0

    def set_interval(self, ms: int) -> None:
        self._timer.setInterval(ms)

    def show_rect(self, rect: QRectF, rgb=(61, 214, 140), delay_ms: int = 0) -> None:
        """rect in logical (Qt) screen coordinates."""
        r = QRectF(rect)
        if r.width() < MIN_W:
            r.adjust(-(MIN_W - r.width()) / 2, 0, (MIN_W - r.width()) / 2, 0)
        if r.height() < MIN_H:
            r.adjust(0, -(MIN_H - r.height()) / 2, 0, (MIN_H - r.height()) / 2)
        r.adjust(-4, -3, 4, 3)  # breathing room around the element
        self.color = QColor(*rgb)
        self.setGeometry(int(r.x() - PAD), int(r.y() - PAD), int(r.width() + 2 * PAD), int(r.height() + 2 * PAD))
        self.fade_start = None
        self._token += 1
        token = self._token

        def go():
            if token != self._token:
                return  # dismissed/cancelled before it appeared
            self.show()
            native.exclude_from_capture(self)
            self._clock.start()
            self._timer.start()

        QTimer.singleShot(max(0, delay_ms), go) if delay_ms else go()

    def dismiss(self) -> None:
        self._token += 1
        if self.isVisible() and self.fade_start is None:
            self.fade_start = self._clock.elapsed() / 1000.0

    def hide_now(self) -> None:
        self._token += 1
        self._timer.stop()
        self.hide()
        self.fade_start = None

    def paintEvent(self, _e) -> None:
        t = self._clock.elapsed() / 1000.0
        alpha = 1.0
        if self.fade_start is not None:
            alpha = 1 - (t - self.fade_start) / 0.35
            if alpha <= 0:
                QTimer.singleShot(0, self.hide_now)
                return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(PAD, PAD, self.width() - 2 * PAD, self.height() - 2 * PAD)
        radius = min(10.0, box.height() / 2.5)
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        draw = _ease_out(t / 0.55)                    # border draws itself in 0.55 s
        appear = _ease_out(t / 0.25)
        breathe = 0.5 + 0.5 * math.sin(max(0.0, t - 0.55) * 3.4)

        c = QColor(self.color)
        # soft fill
        c.setAlphaF(0.10 * appear * alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(path)
        # glow (after the border is complete)
        if draw >= 1:
            for i, w in enumerate((10, 6, 3)):
                g = QColor(self.color)
                g.setAlphaF((0.05 + 0.06 * breathe) * (i + 1) / 3 * alpha)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(g, w))
                p.drawPath(path)
        # border, drawn progressively with a dash trick
        width = 2.2
        length = path.length()
        pen = QPen(QColor(self.color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        col = QColor(self.color)
        col.setAlphaF((0.85 + 0.15 * breathe if draw >= 1 else 1.0) * alpha)
        pen.setColor(col)
        if draw < 1:
            pen.setDashPattern([max(0.01, length * draw / width), length / width + 1])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        # corner sparkles while drawing
        if draw < 1:
            pt = path.pointAtPercent(min(0.999, draw))
            s = QColor(255, 255, 255)
            s.setAlphaF(0.9 * alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(s))
            p.drawEllipse(QPointF(pt), 2.6, 2.6)
        p.end()
