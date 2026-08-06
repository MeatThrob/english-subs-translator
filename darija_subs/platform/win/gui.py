"""Subtitle Translator — desktop control panel (Qt/PySide6).

Feature parity with the macOS AppKit panel: one big On/Off control, a
searchable target-language picker (all 202 NLLB-200 languages, recents
on top, native names shown), the subtitle text size, and a colour wheel
with a hex bar. The translation model loads once at launch and is
reused, so toggling On is instant. Changing language reuses the
already-loaded NLLB model via retarget() — only crossing the
Terjman/NLLB boundary loads weights — and switching while the overlay
is running auto-stops, swaps, and auto-restarts.

On Windows the panel excludes itself from screen capture (the same
WDA_EXCLUDEFROMCAPTURE the overlay uses), so the OCR can never read the
panel's own text as if it were subtitles.

Run:  windows\\gui.bat   (or: .venv\\Scripts\\python -m darija_subs.gui)
"""

import colorsys
import math
import sys
import threading
import time

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import (QApplication, QComboBox, QCompleter, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from ... import langs
from ...gui_common import (config_snapshot as _config_snapshot, hex_to_rgb,
                           rgb_to_hex, wheel_rgb_at, wheel_rgba)
from ...main import finalize_args, parse_args
from ...pipeline import (CACHE_DIR, MODELS_DIR, EventLog, Pipeline,
                         load_config, save_config, saved_region)
from ...translate import Translator
from . import Backend
from .overlay import Overlay, _exclude_from_capture

W, H = 460, 622
WHEEL = 168

GREEN, GREEN_HI = "#29b85c", "#2fc866"
RED, RED_HI = "#d94640", "#e2534d"
GREY = "#6b7078"

STYLE = """
QWidget#root { background: #1b1d21; }
QLabel { color: #e8eaed; }
QLabel#title    { font-size: 25px; font-weight: 700; }
QLabel#subtitle { font-size: 13px; color: rgba(255,255,255,0.55); }
QLabel#privacy  { font-size: 11px; color: rgba(255,255,255,0.38); }
QLabel#status   { font-size: 13px; color: rgba(255,255,255,0.62); }
QLabel#hint     { font-size: 11px; color: rgba(255,255,255,0.38); }
QLabel#caption  { font-size:  9px; font-weight: 700;
                  color: rgba(255,255,255,0.38); letter-spacing: 1px; }
QLabel#setlabel { font-size: 12px; color: rgba(255,255,255,0.62); }
QLabel#sizeval  { font-size: 12px; color: rgba(255,255,255,0.62); }

QFrame#card {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
}
QFrame#swatch { border: 1px solid rgba(255,255,255,0.25); border-radius: 6px; }

QComboBox#lang {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 6px; color: #e8eaed; padding: 3px 8px; font-size: 12px;
}
QComboBox#lang QAbstractItemView {
    background: #24262b; color: #e8eaed;
    selection-background-color: #3b7ddd;
}

QLineEdit#hex {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 6px; color: #e8eaed; padding: 3px;
    font-family: Consolas, monospace; font-size: 12px;
}
QLineEdit#hex:focus { border-color: #3b7ddd; }

QPushButton#preset {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px; color: rgba(255,255,255,0.80); font-size: 11px;
    padding: 4px 0px;
}
QPushButton#preset:hover { background: rgba(255,255,255,0.13); }

QSlider::groove:horizontal {
    height: 4px; background: rgba(255,255,255,0.14); border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #3b7ddd; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #f2f3f5; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 7px;
}
"""


def _power_css(color, hover):
    return (f"QPushButton#power {{ background: {color}; border: none;"
            f" border-radius: 14px; color: white; font-size: 17px;"
            f" font-weight: 600; }}"
            f" QPushButton#power:hover {{ background: {hover}; }}"
            f" QPushButton#power:disabled {{ background: {GREY};"
            f" color: rgba(255,255,255,0.55); }}")


def _dot_css(color):
    return (f"background: {color}; border-radius: 5px;"
            f" min-width: 10px; max-width: 10px;"
            f" min-height: 10px; max-height: 10px;")


def _caption(text):
    lb = QLabel(text)
    lb.setObjectName("caption")
    return lb


class ColorWheel(QWidget):
    """HSV disc — click or drag anywhere to pick a colour."""

    picked = Signal(object)

    def __init__(self, size):
        super().__init__()
        self.setFixedSize(size, size)
        self._size = size
        self._rgb = (1.0, 1.0, 1.0)
        rgba = wheel_rgba(size)
        self._buf = rgba.tobytes()   # keep alive: QImage does not copy
        self._img = QImage(self._buf, size, size, size * 4,
                           QImage.Format.Format_RGBA8888_Premultiplied)
        self.setCursor(Qt.CrossCursor)

    def set_rgb(self, rgb):
        self._rgb = rgb
        self.update()

    def pick_at(self, x, y):
        rgb = wheel_rgb_at(self._size, x, y, y_down=True)
        self._rgb = rgb
        self.update()
        self.picked.emit(rgb)
        return rgb

    def mousePressEvent(self, e):
        p = e.position()
        self.pick_at(p.x(), p.y())

    def mouseMoveEvent(self, e):
        p = e.position()
        self.pick_at(p.x(), p.y())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawImage(self.rect(), self._img)
        h, s, _v = colorsys.rgb_to_hsv(*self._rgb)
        c = self._size / 2.0
        ang = h * 2 * math.pi
        px = c + math.cos(ang) * s * c
        py = c - math.sin(ang) * s * c     # Qt y grows downward
        p.setPen(QPen(QColor(0, 0, 0, 140), 3))
        p.drawEllipse(int(px - 8), int(py - 8), 16, 16)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawEllipse(int(px - 7), int(py - 7), 14, 14)
        p.end()


class Events(QObject):
    """Marshals worker-thread work onto the Qt main thread; also the
    pipeline's ui bridge (show/hide hop through queued signals)."""
    event = Signal(object)
    model_ready = Signal(str, object, bool)   # model_dir, translator, restart
    model_failed = Signal(str)
    show_sig = Signal(str, object)
    hide_sig = Signal()
    toggle_sig = Signal()
    color_sig = Signal(str)
    lang_sig = Signal(str)

    def show(self, text, anchor=None):
        self.show_sig.emit(text, anchor)

    def hide(self):
        self.hide_sig.emit()


class Panel(QWidget):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.cfg = load_config()
        self.lang = args.lang
        self.translator = None
        self.translators = {}      # model_dir -> Translator (kept loaded)
        self.pipe = None
        self.overlay = None
        self.count = 0
        self.state = "loading"
        self.color_hex = self.cfg.get("text_color", "#FFFFFF")

        self.ev = Events()
        self.ev.event.connect(self._apply_event)
        self.ev.model_ready.connect(self._model_ready)
        self.ev.model_failed.connect(self._model_failed)
        self.ev.toggle_sig.connect(self.toggle)
        self.ev.color_sig.connect(self.apply_color)
        self.ev.lang_sig.connect(self._lang_entered)

        self._build()
        self.apply_color(self.color_hex, save=False)
        self._load_model_async(restart=False)

    # ---------- ui ------------------------------------------------
    def _build(self):
        self.setObjectName("root")
        self.setWindowTitle("Subtitle Translator")
        self.setFixedSize(W, H)
        self.setStyleSheet(STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(0)

        for text, name in (("Subtitle Translator", "title"),
                           (f"English  →  {langs.english_name(self.lang)}",
                            "subtitle"),
                           ("Runs entirely on this PC · nothing is uploaded",
                            "privacy")):
            lb = QLabel(text)
            lb.setObjectName(name)
            lb.setAlignment(Qt.AlignCenter)
            if name == "subtitle":
                self.pair_label = lb
            root.addWidget(lb)
            root.addSpacing(4 if name == "title" else 3)
        root.addSpacing(16)

        pill = QHBoxLayout()
        pill.addStretch(1)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(_dot_css(GREY))
        pill.addWidget(self.dot)
        pill.addSpacing(8)
        self.status = QLabel("Loading model…")
        self.status.setObjectName("status")
        pill.addWidget(self.status)
        pill.addStretch(1)
        root.addLayout(pill)
        root.addSpacing(18)

        self.power = QPushButton("Loading…")
        self.power.setObjectName("power")
        self.power.setFixedSize(220, 56)
        self.power.setEnabled(False)
        self.power.setCursor(Qt.PointingHandCursor)
        self.power.setStyleSheet(_power_css(GREY, GREY))
        self.power.clicked.connect(self.toggle)
        prow = QHBoxLayout()
        prow.addStretch(1)
        prow.addWidget(self.power)
        prow.addStretch(1)
        root.addLayout(prow)
        root.addSpacing(8)

        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setAlignment(Qt.AlignCenter)
        root.addWidget(self.hint)
        root.addSpacing(14)

        # ---- language card (type to search all 202 languages)
        lcard = QFrame()
        lcard.setObjectName("card")
        lc = QHBoxLayout(lcard)
        lc.setContentsMargins(16, 12, 16, 12)
        ll = QLabel("Language")
        ll.setObjectName("setlabel")
        ll.setFixedWidth(84)
        lc.addWidget(ll)
        combo = QComboBox()
        combo.setObjectName("lang")
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMaxVisibleItems(14)
        combo.addItems(self._combo_items())
        comp = combo.completer()
        comp.setCompletionMode(QCompleter.PopupCompletion)
        comp.setFilterMode(Qt.MatchContains)          # substring search
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        combo.setCurrentText(langs.label(self.lang))
        combo.activated.connect(self._lang_widget_changed)
        combo.lineEdit().returnPressed.connect(self._lang_widget_changed)
        self.lang_combo = combo
        lc.addWidget(combo, 1)
        root.addWidget(lcard)
        root.addSpacing(14)

        # ---- style card
        card = QFrame()
        card.setObjectName("card")
        c = QVBoxLayout(card)
        c.setContentsMargins(16, 14, 16, 14)
        c.setSpacing(10)
        c.addWidget(_caption("SUBTITLE STYLE"))

        r1 = QHBoxLayout()
        l1 = QLabel("Text size")
        l1.setObjectName("setlabel")
        l1.setFixedWidth(84)
        r1.addWidget(l1)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(24, 80)
        self.size_slider.setValue(int(self.cfg.get("font_size", 42)))
        self.size_slider.valueChanged.connect(self._size_changed)
        r1.addWidget(self.size_slider, 1)
        self.size_value = QLabel(f"{self.size_slider.value()} px")
        self.size_value.setObjectName("sizeval")
        self.size_value.setFixedWidth(44)
        self.size_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        r1.addWidget(self.size_value)
        c.addLayout(r1)

        r2 = QHBoxLayout()
        l2 = QLabel("Colour")
        l2.setObjectName("setlabel")
        l2.setFixedWidth(84)
        r2.addWidget(l2)
        self.hex_field = QLineEdit(self.color_hex)
        self.hex_field.setObjectName("hex")
        self.hex_field.setFixedWidth(104)
        self.hex_field.setAlignment(Qt.AlignCenter)
        self.hex_field.setMaxLength(7)
        self.hex_field.editingFinished.connect(
            lambda: self.apply_color(self.hex_field.text()))
        r2.addWidget(self.hex_field)
        self.swatch = QFrame()
        self.swatch.setObjectName("swatch")
        self.swatch.setFixedSize(42, 24)
        r2.addWidget(self.swatch)
        r2.addStretch(1)
        for text, hx in (("White", "#FFFFFF"), ("Yellow", "#FFD400")):
            b = QPushButton(text)
            b.setObjectName("preset")
            b.setFixedSize(56, 26)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, h=hx: self.apply_color(h))
            r2.addWidget(b)
        c.addLayout(r2)

        self.wheel = ColorWheel(WHEEL)
        self.wheel.picked.connect(
            lambda rgb: self.apply_color(rgb_to_hex(rgb), from_wheel=True))
        wrow = QHBoxLayout()
        wrow.addStretch(1)
        wrow.addWidget(self.wheel)
        wrow.addStretch(1)
        c.addLayout(wrow)
        root.addWidget(card)
        root.addStretch(1)

    def showEvent(self, e):
        super().showEvent(e)
        _exclude_from_capture(self)

    # ---------- colour --------------------------------------------
    def apply_color(self, hex_str, from_wheel=False, save=True):
        rgb = hex_to_rgb(hex_str)
        if rgb is None:                    # reject bad input, keep current
            self.hex_field.setText(self.color_hex)
            return
        self.color_hex = rgb_to_hex(rgb)
        if self.hex_field.text() != self.color_hex:
            self.hex_field.setText(self.color_hex)
        self.swatch.setStyleSheet(
            f"QFrame#swatch {{ background: {self.color_hex};"
            f" border: 1px solid rgba(255,255,255,0.25);"
            f" border-radius: 6px; }}")
        if not from_wheel:
            self.wheel.set_rgb(rgb)
        if save:
            self.cfg["text_color"] = self.color_hex
            save_config(self.cfg)
        if self.overlay is not None:
            self.overlay.set_text_color(self.color_hex)

    def _size_changed(self, v):
        self.size_value.setText(f"{v} px")
        self.cfg["font_size"] = float(v)
        save_config(self.cfg)

    # ---------- language ------------------------------------------
    def _combo_items(self) -> list:
        """Recently-used languages first (current one on top), then the
        full alphabetical list — so switching back to a usual language
        is one dropdown click, not a 202-entry scroll."""
        recents = [c for c in ([self.lang] + self.cfg.get("recent_langs", []))
                   if c in langs.LANGS]
        seen: list[str] = []
        for c in recents:
            if c not in seen:
                seen.append(c)
        return [langs.label(c) for c in seen[:5]] + langs.all_labels()

    def _lang_widget_changed(self, *_):
        self._lang_entered(self.lang_combo.currentText())

    def _lang_entered(self, typed: str):
        if self.lang_combo.currentText() != typed:
            self.lang_combo.setEditText(typed)
        code = langs.resolve(str(typed))
        if code is None:
            self.lang_combo.setEditText(langs.label(self.lang))  # reject
            return
        if code == self.lang or self.state == "loading":
            self.lang_combo.setEditText(langs.label(self.lang))
            return
        self.lang = code
        self.args.lang = code
        self.cfg["target_lang"] = code
        rec = [code] + [c for c in self.cfg.get("recent_langs", [])
                        if c != code]
        self.cfg["recent_langs"] = rec[:5]
        save_config(self.cfg)
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItems(self._combo_items())
        self.lang_combo.setCurrentText(langs.label(code))
        self.lang_combo.blockSignals(False)
        self.pair_label.setText(f"English  →  {langs.english_name(code)}")
        was_on = self.state == "on"
        if was_on:
            self.stop()
        self.state = "loading"
        self.power.setText("Loading…")
        self.power.setEnabled(False)
        self.power.setStyleSheet(_power_css(GREY, GREY))
        self._set_status("Loading model…", GREY)
        self._load_model_async(restart=was_on)

    # ---------- model ---------------------------------------------
    def _model_dir(self) -> str:
        # an explicit --model always wins; otherwise route per language
        if getattr(self.args, "model_explicit", False):
            return self.args.model
        return langs.pick_model(self.lang, MODELS_DIR)

    def _load_model_async(self, restart: bool):
        """Load (or retarget) the model for self.lang off the main
        thread. NLLB is one shared instance across all its languages, so
        switching between two NLLB languages is a cache-file swap, not a
        600 MB load."""
        lang, model_dir = self.lang, self._model_dir()

        def work():
            try:
                tr = self.translators.get(model_dir)
                if tr is None:
                    tr = Translator(
                        model_dir,
                        target_lang=lang,
                        beam_size=self.args.beam,
                        cache_dir=None if self.args.no_cache else CACHE_DIR,
                    )
                    tr.warmup()
                else:
                    tr.retarget(lang)
            except Exception as e:
                self.ev.model_failed.emit(str(e))
                return
            self.ev.model_ready.emit(model_dir, tr, restart)
        threading.Thread(target=work, daemon=True).start()

    def _model_ready(self, model_dir, tr, restart):
        self.translators[model_dir] = tr
        self.translator = tr
        self.args.model = model_dir
        self._set_state("off")
        if restart:
            self.start()

    def _model_failed(self, err):
        self._set_status("Model failed to load", RED)
        self.hint.setText(err[:90])
        self.power.setText("Unavailable")
        self.power.setEnabled(False)

    # ---------- state ---------------------------------------------
    def _set_status(self, text, color):
        self.status.setText(text)
        self.dot.setStyleSheet(_dot_css(color))

    def _set_state(self, state):
        self.state = state
        if state == "off":
            self._set_status("Stopped", RED)
            self.power.setText("Off")
            self.power.setEnabled(True)
            self.power.setStyleSheet(_power_css(RED, RED_HI))
            self.hint.setText("Press the button to start translating")
        else:
            self._set_status("Watching for subtitles", GREEN)
            self.power.setText("On")
            self.power.setEnabled(True)
            self.power.setStyleSheet(_power_css(GREEN, GREEN_HI))
            self.hint.setText(
                "Scanning your saved subtitle region"
                if saved_region(self.cfg) is not None
                else "Scanning the lower part of the screen")

    # ---------- pipeline ------------------------------------------
    def _region(self):
        """Same precedence as the CLI: a saved/picked region from
        config.json wins (with the unplugged-monitor guard); lower half
        of the primary screen otherwise."""
        reg = saved_region(self.cfg)
        if reg is not None:
            return reg, float(self.cfg.get("dpr", 1.0))
        screen = QApplication.primaryScreen()
        g = screen.geometry()
        top = int(g.height() * 0.5)
        return ((g.x(), g.y() + top, g.width(), g.height() - top),
                float(screen.devicePixelRatio()))

    def toggle(self):
        if self.state == "off":
            self.start()
        elif self.state == "on":
            self.stop()

    def start(self):
        if self.translator is None or self.pipe is not None:
            return
        region, dpr = self._region()
        a = self.args
        a.position = "above"
        a.font_size = float(self.cfg.get("font_size", 42.0))

        self.overlay = Overlay(region, position="above",
                               font_size=a.font_size,
                               text_color=self.color_hex,
                               lang=self.lang)
        self.ev.show_sig.connect(self.overlay.show_text)
        self.ev.hide_sig.connect(self.overlay.hide_text)

        log = EventLog(a.log, echo=False, listener=self.ev.event.emit)
        self.pipe = Pipeline(region, a, log, Backend(a, region, dpr),
                             ui=self.ev, translator=self.translator)
        self.count = 0
        self.pipe.start()
        self._set_state("on")

    def stop(self):
        if self.pipe is not None:
            self.pipe.stop_flag.set()
            self.pipe.join(timeout=3)
            self.pipe = None
        if self.overlay is not None:
            try:
                self.ev.show_sig.disconnect(self.overlay.show_text)
                self.ev.hide_sig.disconnect(self.overlay.hide_text)
            except (RuntimeError, TypeError):
                pass
            self.overlay.hide_text()
            self.overlay.deleteLater()
            self.overlay = None
        if self.translator is not None:
            self.translator.save_cache()
        self._set_state("off")

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)

    def _apply_event(self, rec):
        ev = rec.get("event")
        if ev == "show":
            self.count += 1
        elif ev == "capture_failed":
            self._set_status("Screen capture failed", RED)
        elif ev == "ocr_error":
            self._set_status("OCR error — see windows/README.md", RED)


def _wait(pred, timeout=20.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def _self_test(panel, app):
    """Drive the panel from a worker thread via queued signals only —
    never write to a widget from here. Never leaves the user's real
    config.json altered (the handlers under test save to it)."""
    restore = _config_snapshot()
    try:
        _run_self_test(panel, app)
    finally:
        restore()
        app.quit()


def _run_self_test(panel, app):
    ok = True
    t0 = time.time()
    if not _wait(lambda: panel.translator is not None, 240):
        print("FAIL: model never loaded", flush=True)
        return  # caller restores config + quits
    print(f"model loaded in {time.time()-t0:.1f}s; state={panel.state}",
          flush=True)

    # wheel geometry (pure maths, no widget access)
    checks = [((WHEEL + 40, WHEEL / 2), "#FF0000", "3 o'clock"),
              ((-40, WHEEL / 2), "#00FFFF", "9 o'clock"),
              ((WHEEL / 2, WHEEL / 2), "#FFFFFF", "centre")]
    for (x, y), want, where in checks:
        got = rgb_to_hex(wheel_rgb_at(WHEEL, x, y))
        if got != want:
            print(f"FAIL: wheel {where} = {got}, want {want}", flush=True)
            ok = False
    if ok:
        print("wheel geometry ok — rim hues + white centre", flush=True)

    panel.ev.toggle_sig.emit()
    if not _wait(lambda: panel.pipe is not None and panel.state == "on", 15):
        print(f"FAIL: not running after On (state={panel.state})", flush=True)
        ok = False
    elif panel.power.text() != "On":
        print(f"FAIL: running button should read 'On', reads "
              f"{panel.power.text()!r}", flush=True)
        ok = False
    else:
        print(f"ON  ok — pipeline alive={panel.pipe.is_alive()}, "
              f"overlay={panel.overlay is not None}, "
              f"button reads 'On' (green), lines={panel.count}", flush=True)

    panel.ev.color_sig.emit("#FFD400")
    if not _wait(lambda: panel.color_hex == "#FFD400", 5):
        print(f"FAIL: hex bar did not apply ({panel.color_hex})", flush=True)
        ok = False
    else:
        print("colour ok — hex #FFD400 applied to the live overlay",
              flush=True)

    panel.ev.color_sig.emit("nonsense")
    time.sleep(1.0)
    if panel.color_hex != "#FFD400":
        print("FAIL: bad hex was not rejected", flush=True)
        ok = False
    else:
        print("bad hex rejected, previous colour kept", flush=True)

    panel.ev.toggle_sig.emit()
    if not _wait(lambda: panel.pipe is None and panel.state == "off", 15):
        print(f"FAIL: still running after Off (state={panel.state})",
              flush=True)
        ok = False
    elif panel.power.text() != "Off":
        print(f"FAIL: stopped button should read 'Off', reads "
              f"{panel.power.text()!r}", flush=True)
        ok = False
    else:
        print("OFF ok — pipeline stopped, button reads 'Off' (red)",
              flush=True)

    t1 = time.time()
    panel.ev.toggle_sig.emit()
    if not _wait(lambda: panel.pipe is not None and panel.state == "on", 15):
        print("FAIL: could not restart", flush=True)
        ok = False
    else:
        print(f"restart ok in {time.time()-t1:.1f}s (model reused)",
              flush=True)
    panel.ev.toggle_sig.emit()
    _wait(lambda: panel.pipe is None, 10)

    # -- language switching, driven like a user: type a partial name
    # into the combo and confirm (2 gestures: type + Enter) ----------
    from ...pipeline import load_config as _reload_cfg

    def switch_to(typed, want_code, want_state, timeout):
        panel.ev.lang_sig.emit(typed)
        t = time.time()
        while time.time() - t < timeout:
            if (panel.state == want_state and panel.lang == want_code
                    and panel.translator is not None
                    and panel.translator.target_lang == want_code
                    and (want_state == "off"
                         or (panel.pipe is not None
                             and panel.pipe.is_alive()))):
                return time.time() - t
            time.sleep(0.3)
        return None

    def lang_check(typed, want_code, want_state, timeout, expect):
        dt = switch_to(typed, want_code, want_state, timeout)
        if dt is None:
            print(f"FAIL: typing {typed!r} never reached "
                  f"{want_code}/{want_state}", flush=True)
            return False
        combo_now = str(panel.lang_combo.currentText())
        saved = _reload_cfg().get("target_lang")
        sample = panel.translator.translate("Good morning, my friend.")
        if combo_now != langs.label(want_code):
            print(f"FAIL: combo shows {combo_now!r} after {typed!r}",
                  flush=True)
            return False
        if saved != want_code:
            print(f"FAIL: config target_lang={saved!r}, want {want_code}",
                  flush=True)
            return False
        if not expect(sample):
            print(f"FAIL: sample for {want_code}: {sample!r}", flush=True)
            return False
        print(f"lang ok — typed {typed!r} -> {langs.english_name(want_code)} "
              f"({want_state}, {dt:.1f}s) {sample!r}", flush=True)
        return True

    latin = lambda s: bool(s) and all(ord(c) < 0x370 for c in s)
    arab = lambda s: any("؀" <= c <= "ۿ" for c in s)
    cyr = lambda s: any("Ѐ" <= c <= "ӿ" for c in s)

    # off-state: partial English name loads NLLB
    ok &= lang_check("spa", "spa_Latn", "off", 240, latin)
    # informal name routes back to the terjman specialist
    ok &= lang_check("darija", "ary_Arab", "off", 60, arab)

    # realistic case: switching while the overlay is running mid-show —
    # must auto-stop, swap model, and auto-restart without help
    panel.ev.toggle_sig.emit()
    if not _wait(lambda: panel.state == "on", 15):
        print("FAIL: could not turn on before live-switch test", flush=True)
        ok = False
    ok &= lang_check("esp", "spa_Latn", "on", 120, latin)      # native, partial
    ok &= lang_check("русский", "rus_Cyrl", "on", 60, cyr)     # native spelling
    ok &= lang_check("DARIJA", "ary_Arab", "on", 60, arab)     # case-insensitive

    # recents surface at the top of the dropdown, current first
    top3 = [str(panel.lang_combo.itemText(i)) for i in range(3)]
    want_top = [langs.label(c) for c in ("ary_Arab", "rus_Cyrl", "spa_Latn")]
    if top3 != want_top:
        print(f"FAIL: recents order {top3} != {want_top}", flush=True)
        ok = False
    else:
        print(f"recents ok — dropdown starts {top3}", flush=True)

    panel.ev.toggle_sig.emit()
    _wait(lambda: panel.pipe is None, 10)

    print("PASS" if ok else "FAIL", flush=True)
    # app.quit() happens in the caller, after config restore


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    self_test = "--self-test" in argv
    if self_test:
        argv.remove("--self-test")
    start_on = "--start-on" in argv
    if start_on:
        argv.remove("--start-on")

    args = parse_args(argv)
    cfg = load_config()
    args.model_explicit = args.model is not None
    rc = finalize_args(args, cfg)
    if rc:
        return rc

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Subtitle Translator")
    panel = Panel(args)
    panel.show()

    if start_on:
        def _autostart():
            if panel.translator is None:
                threading.Timer(0.5, _autostart).start()
            else:
                panel.ev.toggle_sig.emit()
        threading.Timer(0.5, _autostart).start()
    if self_test:
        threading.Thread(target=_self_test, args=(panel, app),
                         daemon=True).start()

    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
