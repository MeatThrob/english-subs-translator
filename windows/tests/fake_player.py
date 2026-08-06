"""Fake anime player (Qt): cycles timed English subtitle lines.

Usage: fake_player.py --rect X,Y,W,H --log PATH --trigger PATH
Rect is in logical global coords. Waits for the trigger file before
starting, logs show/hide with timestamps.
"""

import argparse
import json
import os
import threading
import time

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

SCRIPT = [
    ("The moon is beautiful tonight, isn't it?", 3.5, 1.2),
    ("Run! The titans have breached the wall!", 3.0, 0.8),
    ("I made this bento just for you, senpai.", 3.5, 1.5),
    ("My sword technique has no equal in this world.", 3.0, 1.0),
    ("Why would you protect someone like me?", 3.0, 1.2),
    ("The moon is beautiful tonight, isn't it?", 2.5, 0.8),  # cache hit
    ("This is our final battle. Give it everything!", 3.5, 1.0),
    ("See you tomorrow at the academy.", 3.0, 1.0),
]


class Bridge(QObject):
    set_text = Signal(str)
    finished = Signal()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rect", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--trigger", required=True)
    a = p.parse_args()
    x, y, w, h = (int(float(v)) for v in a.rect.split(","))

    app = QApplication([])
    win = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    win.setGeometry(x, y, w, h)
    win.setStyleSheet("background-color: black;")
    label = QLabel(win)
    label.setGeometry(0, 0, w, h)
    label.setAlignment(Qt.AlignCenter)
    f = QFont()
    f.setPixelSize(38)
    f.setBold(True)
    label.setFont(f)
    label.setStyleSheet("color: white;")
    win.show()

    logf = open(a.log, "a", encoding="utf-8")

    def emit(event, **kw):
        logf.write(json.dumps({"t": time.time(), "event": event, **kw},
                              ensure_ascii=False) + "\n")
        logf.flush()

    bridge = Bridge()
    bridge.set_text.connect(label.setText)
    bridge.finished.connect(app.quit)

    def run_script():
        while not os.path.exists(a.trigger):
            time.sleep(0.1)
        time.sleep(0.5)
        for text, show_s, gap_s in SCRIPT:
            bridge.set_text.emit(text)
            emit("show", text=text)
            time.sleep(show_s)
            bridge.set_text.emit("")
            emit("hide")
            time.sleep(gap_s)
        emit("done")
        bridge.finished.emit()

    threading.Thread(target=run_script, daemon=True).start()
    app.exec()


if __name__ == "__main__":
    main()
