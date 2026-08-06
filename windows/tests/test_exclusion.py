"""Windows-only self-test: can OCR see THROUGH a capture-excluded
overlay (WDA_EXCLUDEFROMCAPTURE)?

This determines whether `--position cover` is safe on this machine:
  SEE-THROUGH -> cover mode fully works (capture shows the text behind)
  BLACKED-OUT -> capture shows black where the overlay is; use
                 --position above/below (default) instead of cover
  VISIBLE     -> exclusion failed (old Windows 10?); do NOT use cover

Run: .venv\\Scripts\\python tests\\test_exclusion.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if sys.platform != "win32":
    print("SKIP: Windows-only test")
    sys.exit(0)

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from darija_subs.capture import Capture
from darija_subs.ocr import get_backend, lines_to_text
from darija_subs.overlay_qt import _exclude_from_capture

SENTENCE = "EXCLUSION TEST SENTENCE"
COVER = "COVER WINDOW TEXT"


def make_win(x, y, w, h, text, bg, exclude):
    win = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    win.setGeometry(x, y, w, h)
    win.setStyleSheet(f"background-color: {bg};")
    label = QLabel(text, win)
    label.setGeometry(0, 0, w, h)
    label.setAlignment(Qt.AlignCenter)
    f = QFont("Arial")
    f.setPixelSize(36)
    f.setBold(True)
    label.setFont(f)
    label.setStyleSheet("color: white;")
    win.show()
    if exclude:
        _exclude_from_capture(win)
    return win


def main():
    app = QApplication([])
    screen = app.primaryScreen()
    g = screen.geometry()
    dpr = float(screen.devicePixelRatio())
    W, H = 800, 120
    X = g.x() + (g.width() - W) // 2
    Y = g.y() + (g.height() - H) // 2

    behind = make_win(X, Y, W, H, SENTENCE, "black", exclude=False)
    front = make_win(X, Y, W, H, COVER, "rgb(20,20,60)", exclude=True)

    result = {}

    def worker():
        time.sleep(1.5)  # let both windows paint
        cap = Capture((X, Y, W, H), dpr)
        shot = cap.grab()
        if shot is None:
            result["verdict"] = "CAPTURE-FAILED"
            app.quit()
            return
        bgra, w, h = shot
        # average brightness tells black-out apart from see-through
        total = sum(bgra[i] for i in range(0, min(len(bgra), 40000), 41))
        result["brightness"] = total / (min(len(bgra), 40000) / 41)
        try:
            text = lines_to_text(get_backend("winrt").recognize(bgra, w, h))
        except Exception as e:
            result["verdict"] = f"OCR-ERROR: {e}"
            app.quit()
            return
        result["ocr"] = text
        t = text.upper()
        if "EXCLUSION" in t or "SENTENCE" in t:
            result["verdict"] = "SEE-THROUGH"
        elif "COVER" in t:
            result["verdict"] = "VISIBLE"
        elif not t.strip():
            result["verdict"] = "BLACKED-OUT"
        else:
            result["verdict"] = f"UNCLEAR (read: {text!r})"
        cap.close()
        app.quit()

    threading.Thread(target=worker, daemon=True).start()
    app.exec()

    print(f"OCR read: {result.get('ocr', '')!r}")
    print(f"verdict: {result.get('verdict')}")
    v = result.get("verdict", "")
    if v == "SEE-THROUGH":
        print("--position cover is SAFE on this machine.")
    elif v == "BLACKED-OUT":
        print("Use --position above/below (default). Cover would flicker.")
    elif v == "VISIBLE":
        print("WARNING: overlay is visible to capture. Only use the "
              "default 'above' position (no overlap = still correct).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
