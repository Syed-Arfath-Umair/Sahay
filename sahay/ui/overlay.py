"""The on-screen presence: a glowing cursor 'buddy' that trails your mouse, plus a translucent
curved pill that shows Listening / Analyzing / Answering with live animations.

Everything is painted by hand at ~60 fps with frame-rate-independent easing, in a small,
click-through, capture-excluded, always-on-top window that moves with the buddy.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont, QFontMetricsF, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget

from . import native, theme
from .widgets import cursor_path

W, H = 520, 110
TIP_X, TIP_Y = 30, 30          # buddy tip inside the window (normal orientation)
FOLLOW_OFFSET = QPointF(16, 20)  # buddy sits just below-right of the real cursor
PILL_H = 42.0
PILL_MAX = 430.0
ESC_W = 30.0


def _approach(cur: float, target: float, speed: float, dt: float) -> float:
    return cur + (target - cur) * (1 - math.exp(-speed * dt))


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c1, c3 = 1.4, 2.4
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


def _rgba(rgb, a: float) -> QColor:
    c = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.resize(W, H)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._last = 0.0
        self.t = 0.0

        self.state = "hidden"      # listening | typing | analyzing | answering | message
        self.text, self.sub = "", ""
        self.appear, self.appear_target = 0.0, 0.0
        self.pill, self.pill_target = 0.0, 0.0
        self.pill_w = 120.0
        self.level, self.level_target = 0.0, 0.0
        self.color, self.color_target = list(theme.C_LISTEN), list(theme.C_LISTEN)
        self.state_since = 0.0

        self.tip = QPointF(QCursor.pos()) + FOLLOW_OFFSET
        self.follow = True
        self.flight = None           # (start, ctrl, end, t0, duration)
        self.arrived_at: float | None = None
        self.trail: list[tuple[QPointF, float]] = []
        self.flip = False
        self._hide_when_faded = False
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(self.dismiss)

        self.font_main = QFont(theme.FONT_FAMILY, 10)
        self.font_main.setWeight(QFont.Weight.DemiBold)
        self.font_sub = QFont(theme.FONT_FAMILY, 9)

    # ------------------------------------------------------------------ public API
    def summon(self) -> None:
        self._msg_timer.stop()
        self._hide_when_faded = False
        if not self.isVisible():
            self.tip = QPointF(QCursor.pos()) + FOLLOW_OFFSET
            self.appear = 0.0
            self.pill = 0.0
            self.pill_w = 110.0
            self.trail.clear()
            self._place()
            self.show()
            native.exclude_from_capture(self)
            self._clock.start()
            self._last = 0.0
            self._timer.start()
        self.appear_target = 1.0
        self.follow, self.flight, self.arrived_at = True, None, None

    def listening(self) -> None:
        self.summon()
        self._set("listening", "Listening", "", theme.C_LISTEN, pill=True)

    def typing(self) -> None:
        self.summon()
        self._set("typing", "", "", theme.C_TYPE, pill=False)

    def analyzing(self, question: str = "") -> None:
        if not self.isVisible():
            self.summon()
        self._track()
        self._set("analyzing", "Analyzing", question, theme.C_THINK, pill=True)

    def answering(self, label: str = "") -> None:
        self._track()  # keep following until fly_to() takes over
        self._set("answering", "Answering", label, theme.C_ANSWER, pill=True)

    def message(self, text: str, seconds: float = 2.6, rgb=theme.C_WARN) -> None:
        if not self.isVisible():
            self.summon()
        self._set("message", text, "", rgb, pill=True)
        self._msg_timer.start(int(seconds * 1000))

    def set_interval(self, ms: int) -> None:
        """16 ms = 60 fps; 33 ms = 30 fps on battery saver."""
        self._timer.setInterval(ms)

    def set_level(self, v: float) -> None:
        self.level_target = max(0.0, min(1.0, v))

    def fly_to(self, target: QPointF, label: str = "") -> float:
        """Fly the buddy to target (logical coords). Returns the flight duration in seconds."""
        start = QPointF(self.tip)
        d = target - start
        dist = math.hypot(d.x(), d.y())
        if label:
            self.sub = label
        if dist < 4:
            self.tip = QPointF(target)
            self.follow, self.flight, self.arrived_at = False, None, self.t
            return 0.0
        mid = (start + target) / 2
        normal = QPointF(-d.y(), d.x()) / dist
        if normal.y() > 0:          # always arc upwards
            normal = -normal
        ctrl = mid + normal * min(dist * 0.28, 180)
        dur = max(0.55, min(1.15, 0.45 + dist / 2200))
        self.follow = False
        self.arrived_at = None
        self.flight = (start, ctrl, QPointF(target), self.t, dur)
        return dur

    def dismiss(self) -> None:
        self.appear_target = 0.0
        self._hide_when_faded = True

    def hide_now(self) -> None:
        self._timer.stop()
        self.hide()
        self.state = "hidden"
        self.appear = 0.0

    def tip_global(self) -> QPointF:
        return QPointF(self.tip)

    # ------------------------------------------------------------------ internals
    def _track(self) -> None:
        """Resume following the mouse (clears any finished pointing)."""
        if self.flight is None:
            self.follow = True
            self.arrived_at = None
            self.trail.clear()

    def _set(self, state, text, sub, rgb, pill: bool) -> None:
        self.state, self.text, self.sub = state, text, sub
        self.color_target = list(rgb)
        self.pill_target = 1.0 if pill else 0.0
        self.state_since = self.t

    def _place(self) -> None:
        scr = QGuiApplication.screenAt(self.tip.toPoint()) or QGuiApplication.primaryScreen()
        right = scr.availableGeometry().right()
        need = self.tip.x() + 40 + self.pill_w + 20
        if not self.flip and need > right:
            self.flip = True
        elif self.flip and self.tip.x() + 40 + self.pill_w + 60 < right:
            self.flip = False
        left = self.tip.x() - (W - TIP_X) if self.flip else self.tip.x() - TIP_X
        self.move(int(round(left)), int(round(self.tip.y() - TIP_Y)))

    def _local_tip(self) -> QPointF:
        return QPointF(W - TIP_X, TIP_Y) if self.flip else QPointF(TIP_X, TIP_Y)

    def _tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        dt = max(0.001, min(0.05, now - self._last))
        self._last = now
        self.t = now

        self.appear = _approach(self.appear, self.appear_target, 11 if self.appear_target else 9, dt)
        self.pill = _approach(self.pill, self.pill_target, 10, dt)
        self.level = _approach(self.level, self.level_target, 16, dt)
        for i in range(3):
            self.color[i] = _approach(self.color[i], self.color_target[i], 7, dt)
        self.pill_w = _approach(self.pill_w, self._pill_target_width(), 12, dt)

        if self.flight:
            start, ctrl, end, t0, dur = self.flight
            u = (now - t0) / dur
            e = _ease_in_out(u)
            a = start * (1 - e) * (1 - e) + ctrl * 2 * (1 - e) * e + end * e * e
            self.trail.append((QPointF(self.tip), now))
            self.tip = a
            if u >= 1:
                self.tip = QPointF(end)
                self.flight = None
                self.arrived_at = now
        elif self.follow:
            target = QPointF(QCursor.pos()) + FOLLOW_OFFSET
            k = 1 - math.exp(-24 * dt)  # tight, but still silky
            self.tip = self.tip + (target - self.tip) * k
        self.trail = [(p, b) for p, b in self.trail if now - b < 0.28]

        self._place()
        if self._hide_when_faded and self.appear < 0.02:
            self.hide_now()
            return
        self.update()

    def _pill_target_width(self) -> float:
        fm = QFontMetricsF(self.font_main)
        w = 46 + fm.horizontalAdvance(self.text or " ") + 18
        if self.sub:
            fs = QFontMetricsF(self.font_sub)
            w += 10 + min(fs.horizontalAdvance(self.sub), 190)
        if self._show_esc():
            w += ESC_W + 8
        return min(max(w, 110), PILL_MAX)

    def _show_esc(self) -> bool:
        return self.state in ("listening", "analyzing", "answering")

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:
        if self.appear < 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        origin = QPointF(self.pos())
        tip = self._local_tip()
        col = self.color

        # trail while flying
        for pt, born in self.trail:
            age = (self.t - born) / 0.28
            lp = pt - origin
            r = 5 * (1 - age) + 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_rgba(col, 0.35 * (1 - age) * self.appear))
            p.drawEllipse(lp + QPointF(4, 6), r, r)

        # arrival ripples
        if self.arrived_at is not None:
            since = self.t - self.arrived_at
            for k in range(3):
                ph = (since * 0.8 + k / 3) % 1.0
                if since < 0.25 and k:
                    continue
                pen = QPen(_rgba(col, (1 - ph) * 0.7 * self.appear), 2)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                rad = 5 + 26 * ph
                p.drawEllipse(tip, rad, rad)

        self._paint_buddy(p, tip)
        if self.pill > 0.01:
            self._paint_pill(p, tip)
        p.end()

    def _paint_buddy(self, p: QPainter, tip: QPointF) -> None:
        col = self.color
        a = self.appear
        pop = _ease_out_back(min(1.0, a * 1.05))
        breathe = 1 + 0.05 * math.sin(self.t * 3.2)
        if self.state == "listening":
            breathe += 0.18 * self.level
        # glow
        glow_r = 24 * breathe
        g = QRadialGradient(tip + QPointF(6, 9), glow_r)
        g.setColorAt(0, _rgba(col, 0.55 * a))
        g.setColorAt(0.45, _rgba(col, 0.18 * a))
        g.setColorAt(1, _rgba(col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(tip + QPointF(6, 9), glow_r, glow_r)
        # thinking orbit
        if self.state == "analyzing":
            for i in range(3):
                ang = self.t * 4.2 + i * 2.094
                pos = tip + QPointF(6, 9) + QPointF(math.cos(ang) * 17, math.sin(ang) * 17)
                p.setBrush(_rgba(col, (0.9 - i * 0.2) * a))
                p.drawEllipse(pos, 2.2 - i * 0.4, 2.2 - i * 0.4)
        # arrow
        p.save()
        p.translate(tip)
        s = pop * (1 + 0.04 * math.sin(self.t * 3.2))
        p.scale(s, s)
        p.rotate(-4 + 3 * math.sin(self.t * 1.7))
        path = cursor_path(1.0)
        grad = QLinearGradient(0, 0, 14, 22)
        grad.setColorAt(0, _rgba((255, 255, 255), a))
        grad.setColorAt(1, _rgba([min(255, c + 40) for c in col], a))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(_rgba(col, 0.9 * a), 1.3))
        p.drawPath(path)
        p.restore()

    def _paint_pill(self, p: QPainter, tip: QPointF) -> None:
        col = self.color
        vis = self.pill * self.appear
        w, h = self.pill_w, PILL_H
        x = tip.x() - 22 - w if self.flip else tip.x() + 22
        y = tip.y() + 6
        rect = QRectF(x, y, w, h)

        p.save()
        p.setOpacity(min(1.0, vis * 1.2))
        # scale in from the buddy side
        s = 0.86 + 0.14 * _ease_out_back(vis)
        anchor = QPointF(rect.right() if self.flip else rect.left(), rect.center().y())
        p.translate(anchor)
        p.scale(s, s)
        p.translate(-anchor)

        # soft shadow
        p.setPen(Qt.PenStyle.NoPen)
        for i, al in enumerate((0.16, 0.10, 0.06, 0.03)):
            r = rect.adjusted(-i * 2, -i * 2 + 3, i * 2, i * 2 + 3)
            p.setBrush(QColor(0, 0, 0, int(255 * al)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # glass body
        body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        body.setColorAt(0, QColor(30, 33, 44, 225))
        body.setColorAt(1, QColor(16, 18, 26, 215))
        p.setBrush(QBrush(body))
        p.drawRoundedRect(rect, h / 2, h / 2)
        # coloured wash from the icon side
        wash = QLinearGradient(rect.topLeft(), rect.topRight())
        wash.setColorAt(0, _rgba(col, 0.16))
        wash.setColorAt(0.5, _rgba(col, 0.0))
        p.setBrush(QBrush(wash))
        p.drawRoundedRect(rect, h / 2, h / 2)
        # hairline border + top highlight
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
        hl = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + h * 0.5))
        hl.setColorAt(0, QColor(255, 255, 255, 34))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hl), 1))
        p.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), h / 2 - 1, h / 2 - 1)

        # clip content to pill
        clip = QPainterPath()
        clip.addRoundedRect(rect, h / 2, h / 2)
        p.setClipPath(clip)

        icon_c = QPointF(rect.left() + 23, rect.center().y())
        if self.state == "listening":
            self._paint_bars(p, icon_c)
        elif self.state == "analyzing":
            self._paint_spinner(p, icon_c)
        elif self.state == "answering":
            self._paint_waves(p, icon_c)
        else:
            self._paint_dot(p, icon_c)

        # text
        tx = rect.left() + 46
        fm = QFontMetricsF(self.font_main)
        p.setFont(self.font_main)
        main_w = fm.horizontalAdvance(self.text)
        text_rect = QRectF(tx, rect.top(), main_w + 2, h)
        if self.state == "analyzing":
            sweep = ((self.t - self.state_since) * 0.9) % 1.6 - 0.3
            g = QLinearGradient(QPointF(tx, 0), QPointF(tx + main_w, 0))
            base = QColor(236, 238, 243, 150)
            g.setColorAt(0, base)
            g.setColorAt(max(0.0, min(1.0, sweep - 0.18)), base)
            g.setColorAt(max(0.0, min(1.0, sweep)), QColor(255, 255, 255, 255))
            g.setColorAt(max(0.0, min(1.0, sweep + 0.18)), base)
            g.setColorAt(1, base)
            p.setPen(QPen(QBrush(g), 1))
        else:
            p.setPen(QColor(236, 238, 243))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)

        if self.sub:
            p.setFont(self.font_sub)
            fs = QFontMetricsF(self.font_sub)
            sx = tx + main_w + 10
            avail = rect.right() - 18 - sx - (ESC_W + 8 if self._show_esc() else 0)
            if avail > 20:
                shown = fs.elidedText(f"“{self.sub}”" if self.state == "analyzing" else self.sub,
                                      Qt.TextElideMode.ElideRight, avail)
                p.setPen(QColor(160, 168, 184))
                p.drawText(QRectF(sx, rect.top(), avail, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)

        # "esc" key chip: how to cancel
        if self._show_esc():
            chip = QRectF(rect.right() - 12 - ESC_W, rect.center().y() - 9, ESC_W, 18)
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(chip, 5, 5)
            f = QFont(self.font_sub)
            f.setPointSizeF(7.5)
            p.setFont(f)
            p.setPen(QColor(150, 158, 174))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "esc")
        p.restore()

    # -- state visuals (centred at c)
    def _paint_bars(self, p: QPainter, c: QPointF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        n, bw, gap = 5, 3.0, 2.6
        total = n * bw + (n - 1) * gap
        for i in range(n):
            shape = (0.55, 0.85, 1.0, 0.85, 0.55)[i]
            wob = 0.5 + 0.5 * math.sin(self.t * 11 + i * 1.35)
            idle = 3 + 2.2 * (0.5 + 0.5 * math.sin(self.t * 3.1 + i * 0.9))
            hgt = idle + 17 * self.level * shape * (0.45 + 0.55 * wob)
            x = c.x() - total / 2 + i * (bw + gap)
            p.setBrush(_rgba(self.color, 0.95))
            p.drawRoundedRect(QRectF(x, c.y() - hgt / 2, bw, hgt), 1.5, 1.5)

    def _paint_spinner(self, p: QPainter, c: QPointF) -> None:
        r = 8.5
        cg = QConicalGradient(c, -self.t * 320)
        cg.setColorAt(0.0, _rgba(self.color, 1.0))
        cg.setColorAt(0.7, _rgba(self.color, 0.0))
        cg.setColorAt(1.0, _rgba(self.color, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(cg), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(c, r, r)
        pulse = 2.2 + 0.8 * math.sin(self.t * 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_rgba(self.color, 0.9))
        p.drawEllipse(c, pulse, pulse)

    def _paint_waves(self, p: QPainter, c: QPointF) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):
            amp = (4.5 - k * 1.1) * (0.65 + 0.35 * math.sin(self.t * 2.3 + k))
            path = QPainterPath()
            for j in range(23):
                x = c.x() - 11 + j
                env = math.sin(math.pi * j / 22)
                y = c.y() + amp * env * math.sin(j * 0.55 + self.t * (7 + k * 1.7) + k)
                path.moveTo(x, y) if j == 0 else path.lineTo(x, y)
            p.setPen(QPen(_rgba(self.color, 0.95 - k * 0.3), 1.8 - k * 0.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPath(path)

    def _paint_dot(self, p: QPainter, c: QPointF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_rgba(self.color, 0.25))
        p.drawEllipse(c, 8, 8)
        p.setBrush(_rgba(self.color, 1))
        p.drawEllipse(c, 3.5, 3.5)
