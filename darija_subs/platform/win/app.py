"""Windows CLI GUI glue: one QApplication; optionally the picker first,
then overlay + pipeline worker. This is the (previously inline) GUI
section of the old Windows main.py, plus the multilingual pieces:
target_lang is persisted and the overlay is created for the target
language (RTL + script-aware fonts)."""

import signal

from ...pipeline import Pipeline, save_config, saved_region


def run_gui(args, cfg, region, dpr, log, region_from_config=False):
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication

    from . import Backend
    from .overlay import Overlay
    from .picker import Picker

    class Bridge(QObject):
        """Marshals pipeline show/hide onto the Qt main thread."""
        show_sig = Signal(str, object)
        hide_sig = Signal()

        def show(self, text, anchor=None):
            self.show_sig.emit(text, anchor)

        def hide(self):
            self.hide_sig.emit()

    import sys as _sys
    app = QApplication(_sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    state = {"pipe": None, "overlay": None, "picker": None, "bridge": None}

    # A region loaded from config.json could not be checked against the
    # display list before Qt existed (headless-safe path in main). Now
    # that it does exist, apply the same unplugged-monitor guard macOS
    # applies, falling back to the auto-scan below.
    if region is not None and region_from_config:
        if saved_region(cfg) is None:
            region = None

    def start_pipeline(reg, reg_dpr):
        cfg["region"] = list(reg)
        cfg["dpr"] = reg_dpr
        cfg["position"] = args.position
        cfg["font_size"] = args.font_size
        cfg["target_lang"] = args.lang
        save_config(cfg)
        bridge = Bridge()
        overlay = Overlay(reg, position=args.position,
                          font_size=args.font_size, lang=args.lang)
        bridge.show_sig.connect(overlay.show_text)
        bridge.hide_sig.connect(overlay.hide_text)
        state["overlay"] = overlay
        state["bridge"] = bridge
        state["pipe"] = Pipeline(reg, args, log, Backend(args, reg, reg_dpr),
                                 ui=bridge)
        state["pipe"].start()

    def on_picked(reg, reg_dpr):
        state["picker"] = None
        if reg is None:
            log.emit("cancelled")
            app.quit()
            return
        log.emit("region_selected", region=list(reg), dpr=reg_dpr)
        start_pipeline(reg, reg_dpr)

    if args.pick:
        state["picker"] = Picker(on_picked)
        state["picker"].start()
    elif region is not None:
        start_pipeline(region, dpr or 1.0)
    else:
        s_ = app.primaryScreen()
        g = s_.geometry()
        band = 0.5 if args.band is None else args.band
        top = int(g.height() * band)
        auto_region = (g.x(), g.y() + top, g.width(), g.height() - top)
        start_pipeline(auto_region, float(s_.devicePixelRatio()))

    # let Ctrl+C work under the Qt event loop
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(200)

    app.exec()
    if state["pipe"] is not None:
        state["pipe"].stop_flag.set()
        state["pipe"].join(timeout=3)
    return 0
