"""Reusable minimal widgets: Toggle switch, Segmented control, settings rows, logo."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def cursor_path(scale: float = 1.0) -> QPainterPath:
    """Classic pointer arrow, tip at (0, 0)."""
    pts = [(0, 0), (0, 19), (5, 14.6), (8.6, 22.6), (11.8, 21.2), (8.3, 13.4), (14.5, 13.4)]
    path = QPainterPath(QPointF(0, 0))
    for x, y in pts[1:]:
        path.lineTo(x * scale, y * scale)
    path.closeSubpath()
    return path


def logo_pixmap(size: int = 64) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(theme.ACCENT))
    g.setColorAt(1, QColor(theme.ACCENT2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    s = size / 34
    p.translate(size * 0.34, size * 0.22)
    p.setBrush(QColor("white"))
    p.drawPath(cursor_path(s))
    p.end()
    return pm


def logo_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(s))
    return icon


class Toggle(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_mix(QColor(theme.SURFACE3), QColor(theme.ACCENT), self._pos))
        p.drawRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(3 + self._pos * 16, 3, 16, 16))


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("segbtn")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)

    def set(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


def section(title: str) -> tuple[QLabel, QFrame, QVBoxLayout]:
    label = QLabel(title.upper())
    label.setObjectName("sectionTitle")
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 4, 16, 4)
    lay.setSpacing(0)
    return label, card, lay


def setting_row(title: str, desc: str, control: QWidget, stacked: bool = False) -> QWidget:
    """One setting: title + description on the left, control on the right (or below when stacked)."""
    w = QWidget()
    outer = QVBoxLayout(w) if stacked else QHBoxLayout(w)
    outer.setContentsMargins(0, 12, 0, 12)
    outer.setSpacing(8 if stacked else 16)
    text = QVBoxLayout()
    text.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("rowTitle")
    text.addWidget(t)
    if desc:
        d = QLabel(desc)
        d.setObjectName("rowDesc")
        d.setWordWrap(True)
        text.addWidget(d)
    outer.addLayout(text, 1)
    if stacked:
        outer.addWidget(control)
    else:
        outer.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return w


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    return f


class GlowDot(QWidget):
    """Tiny animated status dot for the header."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.color = QColor(color)

    def set_color(self, color: str) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(6, 6), 6)
        c = QColor(self.color)
        c.setAlphaF(0.35)
        g.setColorAt(0, c)
        c2 = QColor(self.color)
        c2.setAlphaF(0.0)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(0, 0, 12, 12))
        p.setBrush(self.color)
        p.drawEllipse(QRectF(3.5, 3.5, 5, 5))
        p.setPen(QPen(Qt.PenStyle.NoPen))
