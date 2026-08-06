"""Windows platform: mss capture, WinRT OCR, Qt/PySide6 overlay + GUI.

EXPERIMENTAL — the Qt pieces are exercised on macOS (set
DARIJA_SUBS_PLATFORM=win), but the Windows-only syscalls (WinRT OCR,
WDA_EXCLUDEFROMCAPTURE) have never run on real Windows hardware.
See windows/FINISHING.md."""

DEFAULT_FONT_SIZE = 42.0
SETUP_HINT = "windows\\setup.bat"


def check_permission():
    return None   # Windows needs no screen-recording permission


def display_bounds():
    """Screen rects in logical coordinates, or None when Qt is not up
    yet (headless CLI) — the caller then assumes a saved region is
    usable, exactly like macOS does when its display list is empty."""
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return None
    if QGuiApplication.instance() is None:
        return None
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out or None


def auto_region(band):
    """Resolved later, inside run_gui, once a QApplication exists."""
    return None


class Backend:
    """Pipeline backend over mss capture + pluggable OCR (see
    pipeline.py for the contract)."""

    def __init__(self, args, region, dpr=None):
        self.region = tuple(region)
        self.dpr = float(dpr) if dpr else 1.0
        self._ocr_choice = getattr(args, "ocr", "auto")
        self.name = self._ocr_choice
        self.capture_failed_info = {}
        self._backend = None
        self._cap = None

    def open(self):
        from .capture import Capture
        from .ocr import get_backend
        self._backend = get_backend(self._ocr_choice)  # raises -> fatal
        self.name = type(self._backend).__name__
        self._cap = Capture(self.region, self.dpr)

    def grab(self):
        return self._cap.grab()

    def ocr(self, frame, fast=False):
        bgra, w, h = frame
        return self._backend.recognize(bgra, w, h)

    def pixels(self, frame):
        bgra, w, h = frame
        return bgra, w * 4, w, h

    def close(self):
        if self._cap is not None:
            self._cap.close()


def make_backend(args, region, dpr=None):
    return Backend(args, region, dpr)


def run_gui(args, cfg, region, dpr, log, region_from_config=False):
    from .app import run_gui as _run
    return _run(args, cfg, region, dpr, log,
                region_from_config=region_from_config)


def gui_main(argv=None):
    from .gui import main as _main
    return _main(argv)
