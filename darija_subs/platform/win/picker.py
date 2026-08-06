"""Full-screen drag-to-select region picker (Qt).

Covers the screen under the mouse cursor; the user drags a box around
the English subtitle area. Emits (x, y, w, h) in *logical* global
coordinates plus the screen's devicePixelRatio, or None on Esc.
"""

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Picker(QWidget):
    def __init__(self, callback):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))
        self._callback = callback
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        self._screen = screen
        self.setGeometry(screen.geometry())

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _sel(self) -> QRect | None:
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()
            self._callback(None, None)

    def mousePressEvent(self, ev):
        self._origin = ev.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, ev):
        if self._origin is not None:
            self._current = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev):
        self._current = ev.position().toPoint()
        r = self._sel()
        if r is None or r.width() < 150 or r.height() < 40:
            self._origin = self._current = None
            self.update()
            return
        g = self.geometry().topLeft()
        region = (r.x() + g.x(), r.y() + g.y(), r.width(), r.height())
        self.close()
        self._callback(region, float(self._screen.devicePixelRatio()))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        r = self._sel()
        if r is not None:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 220, 90), 2))
            p.drawRect(r)
        p.setPen(QColor(255, 255, 255))
        f = p.font()
        f.setPointSize(16)
        f.setBold(True)
        p.setFont(f)
        p.drawText(
            self.rect().adjusted(0, 40, 0, 0),
            Qt.AlignHCenter | Qt.AlignTop,
            "Drag a box around the ENGLISH subtitle area  —  Esc to cancel",
        )
        p.end()
