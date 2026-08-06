"""Always-on-top, click-through translated-subtitle overlay (Qt).

Qt/HarfBuzz gives correct complex-script shaping (Arabic ligatures,
Indic conjuncts) on every platform. The target language decides:

  - paragraph direction: RTL for Arabic/Hebrew scripts, LTR otherwise
    (langs.is_rtl, same rule as the macOS overlay);
  - the font: a per-script cascade of Windows system fonts (Segoe UI /
    Yu Gothic / Malgun Gothic / Microsoft YaHei / JhengHei / Nirmala UI /
    Leelawadee UI / Myanmar Text / Ebrima / Microsoft Himalaya), with
    Segoe UI + the OS fallback chain behind it. The macOS-only fonts
    (SF Arabic, Geeza Pro) intentionally do not appear here.
    windows/tests/test_win_fonts.py checks the cascade actually
    resolves glyphs (no tofu) on the machine it runs on.

The translation anchors dynamically to the detected English text (a
logical global rect), with light smoothing so it doesn't jitter. On
Windows the window is excluded from screen capture via
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) so the OCR can never
read its own output (run windows/tests/test_exclusion.py on the target
machine to confirm before trusting `--position cover`).
"""

import sys

from PySide6.QtCore import Qt, QRect, Slot
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget

from ... import langs

WDA_EXCLUDEFROMCAPTURE = 0x11
PAD_X, PAD_Y = 18, 10
_SMOOTH_Y = 22
_SMOOTH_X = 55

# Windows system fonts per NLLB script code (the code suffix of the
# language, e.g. spa_Latn -> Latn). Anything not listed uses Segoe UI,
# which covers Latin, Cyrillic, Greek, Arabic, Hebrew, Armenian and
# Georgian on Windows 10/11.
_SCRIPT_FONTS = {
    "Jpan": ["Yu Gothic UI", "Yu Gothic", "Meiryo UI"],
    "Hang": ["Malgun Gothic"],
    "Hans": ["Microsoft YaHei UI", "Microsoft YaHei"],
    "Hant": ["Microsoft JhengHei UI", "Microsoft JhengHei"],
    "Deva": ["Nirmala UI"], "Beng": ["Nirmala UI"], "Guru": ["Nirmala UI"],
    "Gujr": ["Nirmala UI"], "Orya": ["Nirmala UI"], "Taml": ["Nirmala UI"],
    "Telu": ["Nirmala UI"], "Knda": ["Nirmala UI"], "Mlym": ["Nirmala UI"],
    "Sinh": ["Nirmala UI"],
    "Thai": ["Leelawadee UI"], "Laoo": ["Leelawadee UI"],
    "Khmr": ["Leelawadee UI"],
    "Mymr": ["Myanmar Text"],
    "Ethi": ["Ebrima"],
    "Tfng": ["Ebrima"],
    "Tibt": ["Microsoft Himalaya"],
}


def font_for(lang_code: str, pixel_size: float) -> QFont:
    """A QFont whose family cascade resolves the target language's
    script on Windows. Qt walks the families list per character, then
    the OS fallback chain."""
    families = list(_SCRIPT_FONTS.get(langs.script(lang_code), []))
    if "Segoe UI" not in families:
        families.append("Segoe UI")
    families.append("Arial")
    f = QFont(families[0])
    f.setFamilies(families)
    f.setPixelSize(int(pixel_size))
    f.setWeight(QFont.Weight.Black)
    return f


def _exclude_from_capture(widget: QWidget):
    """Make the window invisible to screen capture.

    Windows: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) — never
    executed on real Windows yet (test_exclusion.py verifies it there).
    macOS (validation runs of this Qt stack): the same guarantee via
    NSWindowSharingNone, reached through the Qt winId's NSView — without
    it the mac-side e2e feeds the overlay back into OCR for Latin-script
    targets, exactly the failure the Windows call is supposed to
    prevent."""
    if sys.platform == "win32":
        import ctypes
        hwnd = int(widget.winId())
        ctypes.windll.user32.SetWindowDisplayAffinity(
            ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE
        )
    elif sys.platform == "darwin":
        try:
            import objc   # pyobjc, present in the mac validation venv
            nsview = objc.objc_object(c_void_p=int(widget.winId()))
            nsview.window().setSharingType_(0)   # NSWindowSharingNone
        except Exception:
            pass   # validation-only aid; never fatal


class Overlay(QWidget):
    def __init__(self, region_logical, position: str = "above",
                 font_size: float = 42.0, gap: int = 6,
                 text_color: str = "#FFFFFF",
                 lang: str = langs.DEFAULT_TARGET):
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
        self.lang = lang
        self._rtl = langs.is_rtl(lang)
        self._excluded = False
        self._anchor = None

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setLayoutDirection(
            Qt.RightToLeft if self._rtl else Qt.LeftToRight)
        self.label.setFont(font_for(lang, font_size))
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
    def show_text(self, text: str, anchor=None):
        rx, ry, rw, rh = (int(v) for v in self._smoothed(anchor))
        fm = QFontMetrics(self.label.font())
        max_w = int(max(min(rw * 1.6, self.region[2]), 320))
        br = fm.boundingRect(
            QRect(0, 0, max_w - 2 * PAD_X, 4000),
            Qt.TextWordWrap | Qt.AlignCenter,
            text,
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

        self.label.setText(text)
        if not self.isVisible():
            self.show()

    @Slot()
    def hide_text(self):
        if self.isVisible():
            self.hide()
        self._anchor = None
