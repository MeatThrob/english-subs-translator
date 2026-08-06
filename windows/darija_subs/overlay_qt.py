"""Always-on-top, click-through Arabic overlay (Qt).

Qt/HarfBuzz gives correct Arabic shaping + RTL on every platform.
The Arabic anchors dynamically to the detected English text (a logical
global rect), with light smoothing so it doesn't jitter. On Windows the
window is excluded from screen capture via
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) so the OCR can never
read its own output (run tests/test_exclusion.py on the target machine
to confirm before trusting `--position cover`).
"""

import sys

from PySide6.QtCore import Qt, QRect, Slot
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget

WDA_EXCLUDEFROMCAPTURE = 0x11
PAD_X, PAD_Y = 18, 10
_SMOOTH_Y = 22
_SMOOTH_X = 55


def _exclude_from_capture(widget: QWidget):
    if sys.platform != "win32":
        return
    import ctypes
    hwnd = int(widget.winId())
    ctypes.windll.user32.SetWindowDisplayAffinity(
        ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE
    )


class Overlay(QWidget):
    def __init__(self, region_logical, position: str = "above",
                 font_size: float = 42.0, gap: int = 6,
                 text_color: str = "#FFFFFF"):
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.region = tuple(int(v) for v in region_logical)
        self.position = position
        self.gap = gap
        self.text_color = text_color or "#FFFFFF"
        self._excluded = False
        self._anchor = None

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setLayoutDirection(Qt.RightToLeft)
        f = QFont("Segoe UI")
        f.setPixelSize(int(font_size))
        f.setWeight(QFont.Weight.Black)
        self.label.setFont(f)
        self._alpha = 235 if position == "cover" else 185
        self._radius = 0 if position == "cover" else 8
        self._restyle()

    def _restyle(self):
        self.label.setStyleSheet(
            f"color: {self.text_color};"
            f" background-color: rgba(0,0,0,{self._alpha});"
            f" border-radius: {self._radius}px;"
        )

    @Slot(str)
    def set_text_color(self, hex_str: str):
        self.text_color = hex_str or "#FFFFFF"
        self._restyle()

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._excluded:
            _exclude_from_capture(self)
            self._excluded = True

    def _smoothed(self, anchor):
        if anchor is None:
            return self._anchor or self.region
        if self._anchor is not None:
            ax, ay, aw, ah = self._anchor
            nx, ny, nw, nh = anchor
            if (abs(ny - ay) < _SMOOTH_Y
                    and abs((ny + nh) - (ay + ah)) < _SMOOTH_Y
                    and abs((nx + nw / 2) - (ax + aw / 2)) < _SMOOTH_X):
                return self._anchor
        self._anchor = anchor
        return anchor

    @Slot(str, object)
    def show_text(self, arabic: str, anchor=None):
        rx, ry, rw, rh = (int(v) for v in self._smoothed(anchor))
        fm = QFontMetrics(self.label.font())
        max_w = int(max(min(rw * 1.6, self.region[2]), 320))
        br = fm.boundingRect(
            QRect(0, 0, max_w - 2 * PAD_X, 4000),
            Qt.TextWordWrap | Qt.AlignCenter,
            arabic,
        )
        tw = min(max_w, br.width() + 2 * PAD_X)
        th = br.height() + 2 * PAD_Y

        if self.position == "cover":
            pad = 8
            self.setGeometry(rx - pad, ry - pad, rw + 2 * pad, rh + 2 * pad)
            self.label.setGeometry(0, 0, rw + 2 * pad, rh + 2 * pad)
        else:
            wx = rx + (rw - tw) // 2
            if self.position == "below":
                wy = ry + rh + self.gap
            else:  # above
                wy = ry - th - self.gap
            self.setGeometry(wx, wy, tw, th)
            self.label.setGeometry(0, 0, tw, th)

        self.label.setText(arabic)
        if not self.isVisible():
            self.show()

    @Slot()
    def hide_text(self):
        if self.isVisible():
            self.hide()
        self._anchor = None
