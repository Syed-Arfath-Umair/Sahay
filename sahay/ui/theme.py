"""Design tokens + Qt stylesheet. Minimal dark theme, one accent."""

BG = "#0D0E12"
SURFACE = "#15171D"
SURFACE2 = "#1B1E26"
SURFACE3 = "#2A2E38"
BORDER = "rgba(255,255,255,0.07)"
TEXT = "#ECEEF3"
MUTED = "#8A92A3"
FAINT = "#5C6373"
ACCENT = "#6C7CFF"
ACCENT2 = "#A36BFF"
GREEN = "#3DD68C"
AMBER = "#F5B84B"
RED = "#FF6B6B"

# State colours for the overlay (r, g, b)
C_LISTEN = (77, 190, 255)
C_THINK = (163, 107, 255)
C_ANSWER = (61, 214, 140)
C_TYPE = (108, 124, 255)
C_WARN = (245, 184, 75)

FONT_FAMILY = "Segoe UI Variable Text"
FONT_FALLBACK = "Segoe UI"

QSS = f"""
* {{ font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}"; color: {TEXT}; }}
QWidget#root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#faint {{ color: {FAINT}; font-size: 11px; }}
QLabel#sectionTitle {{ color: {FAINT}; font-size: 11px; font-weight: 600; letter-spacing: 1px;
                       padding: 18px 4px 6px 4px; }}
QLabel#rowTitle {{ font-size: 13px; }}
QLabel#rowDesc {{ color: {MUTED}; font-size: 11px; }}
QLabel#emptyTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#kbd {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 6px;
             padding: 3px 8px; font-size: 12px; color: {TEXT}; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

QWidget#segmented {{ background: {SURFACE2}; border-radius: 10px; }}
QPushButton#segbtn {{ background: transparent; border: none; border-radius: 8px; padding: 6px 12px;
                     color: {MUTED}; font-size: 12px; }}
QPushButton#segbtn:hover {{ color: {TEXT}; }}
QPushButton#segbtn:checked {{ background: {SURFACE3}; color: {TEXT}; }}

QPushButton#tab {{ background: transparent; border: none; padding: 6px 4px; margin-right: 14px;
                  color: {MUTED}; font-size: 13px; border-bottom: 2px solid transparent; }}
QPushButton#tab:hover {{ color: {TEXT}; }}
QPushButton#tab:checked {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QPushButton#ghost {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px;
                    padding: 7px 14px; font-size: 12px; }}
QPushButton#ghost:hover {{ background: {SURFACE3}; }}
QPushButton#send {{ background: {ACCENT}; border: none; border-radius: 17px; min-width: 34px; max-width: 34px;
                   min-height: 34px; max-height: 34px; font-size: 15px; color: white; }}
QPushButton#send:hover {{ background: #7D8BFF; }}
QPushButton#send:disabled {{ background: {SURFACE3}; color: {FAINT}; }}

QLineEdit, QComboBox {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px;
                       padding: 7px 10px; font-size: 12px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#chatInput {{ background: {SURFACE}; border-radius: 18px; padding: 9px 16px; font-size: 13px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px;
                              selection-background-color: {SURFACE3}; outline: none; padding: 4px; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; height: 0; }}

QFrame#userBubble {{ background: {ACCENT}; border-radius: 16px; }}
QFrame#aiBubble {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px; }}
QLabel#bubbleText {{ font-size: 13px; }}
QLabel#bubbleMeta {{ color: {MUTED}; font-size: 11px; }}

QLabel#toast {{ background: {SURFACE3}; border-radius: 12px; padding: 6px 14px; font-size: 12px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; font-size: 12px; }}
QMenu::item:selected {{ background: {SURFACE3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
"""
