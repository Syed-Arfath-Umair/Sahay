"""Glass text box that appears beside the buddy when you don't speak within 3 seconds."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QWidget

from . import native, theme

BW, BH = 440, 64
PH = 46


class TypeBox(QWidget):
    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(BW, BH)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Ask about anything on your screen…")
        self.edit.setFrame(False)
        f = QFont(theme.FONT_FAMILY, 11)
        self.edit.setFont(f)
        self.edit.setStyleSheet(
            "QLineEdit { background: transparent; border: none; color: #ECEEF3; padding: 0;"
            f" selection-background-color: {theme.ACCENT}; }}")
        self.edit.setGeometry(22, (BH - PH) // 2 + 8, BW - 80, PH - 16)
        self.edit.returnPressed.connect(self._submit)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def open_at(self, tip: QPointF, flip: bool = False) -> None:
        x = tip.x() - 22 - BW if flip else tip.x() + 18
        self.move(int(x), int(tip.y() + 2))
        self.edit.clear()
        self.setWindowOpacity(0.0)
        self.show()
        native.exclude_from_capture(self)
        native.force_foreground(self)
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def close_box(self) -> None:
        self.hide()
        self.edit.clear()

    def _submit(self) -> None:
        t = self.edit.text().strip()
        if t:
            self.close_box()
            self.submitted.emit(t)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_box()
            self.cancelled.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, (BH - PH) / 2, BW - 8, PH)
        p.setPen(Qt.PenStyle.NoPen)
        for i, al in enumerate((0.18, 0.10, 0.05)):
            r = rect.adjusted(-i * 2, -i * 2 + 3, i * 2, i * 2 + 3)
            p.setBrush(QColor(0, 0, 0, int(255 * al)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor(30, 33, 44, 238))
        g.setColorAt(1, QColor(17, 19, 27, 232))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(rect, PH / 2, PH / 2)
        focus = QColor(theme.ACCENT)
        focus.setAlpha(150 if self.edit.hasFocus() else 60)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(focus, 1.2))
        p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), PH / 2, PH / 2)
        # enter hint chip
        chip = QRectF(rect.right() - 44, rect.center().y() - 12, 30, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(chip, 8, 8)
        p.setPen(QColor(160, 168, 184))
        p.setFont(QFont(theme.FONT_FAMILY, 10))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "↵")
