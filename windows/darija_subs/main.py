"""darija-subs (Windows/Qt port): live English-subtitle -> Moroccan
Arabic (Darija) overlay. All local, no network at runtime.

Usage:
  run.bat --pick        # first run: drag-select the subtitle area, then it starts
  run.bat               # reuse saved region
  run.bat --headless --region X,Y,W,H --log f.jsonl   # testing, no overlay
"""

import argparse
import json
import os
import signal
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
DEFAULT_MODEL = os.path.join(PROJECT_ROOT, "models", "terjman-nano-int8")
DEFAULT_CACHE = os.path.join(PROJECT_ROOT, "cache.json")

from . import subfilter, textnorm
from .capture import Capture
from .ocr import get_backend, lines_to_text
from .translate import Translator


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class EventLog:
    def __init__(self, path: str | None, echo: bool = True, listener=None):
        self.path = path
        self.echo = echo
        self.listener = listener   # called with the event dict (GUI hook)
        self._f = open(path, "a", encoding="utf-8") if path else None
        self._lock = threading.Lock()

    def emit(self, event: str, **kw):
        rec = {"t": time.time(), "event": event, **kw}
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            if self._f:
                self._f.write(line + "\n")
                self._f.flush()
            if self.echo:
                print(line, flush=True)
        if self.listener is not None:
            try:
                self.listener(rec)
            except Exception:
                pass


class Pipeline(threading.Thread):
    """Capture -> OCR -> translate loop. UI updates go through Qt
    signals (queued to the main thread) when a bridge is attached."""

    def __init__(self, region, dpr, args, log: EventLog, bridge=None,
                 translator=None):
        super().__init__(daemon=True)
        self.region = tuple(region)
        self.dpr = dpr
        self.args = args
        self.log = log
        self.bridge = bridge
        self.translator = translator   # preloaded model (GUI reuses it)
        self.stop_flag = threading.Event()

    def run(self):
        a = self.args
        try:
            backend = get_backend(a.ocr)
        except Exception as e:
            self.log.emit("fatal", error=f"OCR backend failed: {e}")
            os._exit(2)
        translator = self.translator
        if translator is None:
            try:
                translator = Translator(
                    a.model,
                    beam_size=a.beam,
                    cache_path=None if a.no_cache else DEFAULT_CACHE,
                )
                translator.warmup()
            except Exception as e:
                self.log.emit("fatal", error=f"translator load failed: {e}")
                os._exit(2)

        cap = Capture(self.region, self.dpr)
        self.log.emit(
            "ready", model=a.model, ocr=type(backend).__name__,
            region=list(self.region), dpr=self.dpr,
        )

        committed = None
        empty_streak = 0
        grab_failures = 0
        interval = a.interval / 1000.0

        while not self.stop_flag.is_set():
            t0 = time.perf_counter()
            shot = cap.grab()
            if shot is None:
                grab_failures += 1
                if grab_failures in (1, 50):
                    self.log.emit("capture_failed")
                time.sleep(0.5)
                continue
            grab_failures = 0
            bgra, w, h = shot

            t1 = time.perf_counter()
            try:
                lines = backend.recognize(bgra, w, h)
            except Exception as e:
                self.log.emit("ocr_error", error=str(e))
                time.sleep(0.5)
                continue
            scale_x = w / max(1.0, self.region[2])
            scale_y = h / max(1.0, self.region[3])
            lines, ubox = subfilter.filter_lines(bgra, w, h, lines, scale_y)
            text = textnorm.normalize(lines_to_text(lines))
            ocr_ms = (time.perf_counter() - t1) * 1000

            anchor = None
            if ubox is not None:
                ax, ay, aw, ah = ubox
                anchor = (
                    self.region[0] + ax / scale_x,
                    self.region[1] + ay / scale_y,
                    aw / scale_x,
                    ah / scale_y,
                )

            if len(text) > 220:
                # not a subtitle — region is probably over the wrong thing
                text = ""
            if not text:
                empty_streak += 1
                if committed is not None and empty_streak >= a.hide_frames:
                    committed = None
                    if self.bridge:
                        self.bridge.hide_sig.emit()
                    self.log.emit("hide")
            else:
                empty_streak = 0
                if text != committed:
                    t2 = time.perf_counter()
                    arabic = translator.translate(text)
                    tr_ms = (time.perf_counter() - t2) * 1000
                    committed = text
                    if arabic:
                        if self.bridge:
                            self.bridge.show_sig.emit(arabic, anchor)
                        self.log.emit(
                            "show", en=text, ar=arabic,
                            ocr_ms=round(ocr_ms, 1), tr_ms=round(tr_ms, 1),
                        )

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, interval - elapsed))

        translator.save_cache()
        cap.close()


def parse_args(argv):
    p = argparse.ArgumentParser(prog="darija-subs", description=__doc__)
    p.add_argument("--pick", action="store_true",
                   help="manually drag-select a region instead of the "
                        "automatic lower-half scan")
    p.add_argument("--band", type=float, default=0.5,
                   help="auto-scan band: fraction of screen height where "
                        "the scan starts (default 0.5 = lower half)")
    p.add_argument("--region", type=str,
                   help="x,y,w,h logical screen coords (top-left origin)")
    p.add_argument("--dpr", type=float, default=None,
                   help="devicePixelRatio for --region (default: saved or 1.0)")
    p.add_argument("--position", choices=["above", "below", "cover"], default=None)
    p.add_argument("--font-size", type=float, default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--beam", type=int, default=2)
    p.add_argument("--ocr", default="auto",
                   choices=["auto", "winrt", "vision", "rapid"])
    p.add_argument("--interval", type=int, default=50)
    p.add_argument("--hide-frames", type=int, default=2)
    p.add_argument("--turbo", action="store_true",
                   help="lowest latency: near-continuous OCR and hide on "
                        "first empty read; higher CPU use")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--log", type=str, default=None)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config()

    if args.turbo:
        if args.interval == 50:
            args.interval = 25
        if args.hide_frames == 2:
            args.hide_frames = 1

    if args.position is None:
        args.position = cfg.get("position", "above")
    if args.font_size is None:
        args.font_size = cfg.get("font_size", 42.0)

    if not os.path.isdir(args.model):
        print(f"ERROR: translation model not found at {args.model}\n"
              f"Copy models/terjman-nano-int8 from the distribution zip.",
              file=sys.stderr)
        return 2

    region = None
    dpr = args.dpr
    if args.region:
        region = tuple(float(v) for v in args.region.split(","))
        if dpr is None:
            dpr = 1.0
    elif not args.pick and "region" in cfg:
        region = tuple(cfg["region"])
        if dpr is None:
            dpr = float(cfg.get("dpr", 1.0))

    log = EventLog(args.log, echo=not args.quiet)

    if args.headless:
        if region is None:
            print("ERROR: --headless needs --region or a saved config",
                  file=sys.stderr)
            return 2
        pipe = Pipeline(region, dpr or 1.0, args, log, bridge=None)
        try:
            pipe.run()  # blocking; Ctrl+C to stop
        except KeyboardInterrupt:
            pass
        return 0

    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication
    from .overlay_qt import Overlay
    from .picker_qt import Picker

    class Bridge(QObject):
        show_sig = Signal(str, object)
        hide_sig = Signal()

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    state = {"pipe": None, "overlay": None, "picker": None}

    def start_pipeline(reg, reg_dpr):
        cfg["region"] = list(reg)
        cfg["dpr"] = reg_dpr
        cfg["position"] = args.position
        cfg["font_size"] = args.font_size
        save_config(cfg)
        bridge = Bridge()
        overlay = Overlay(reg, position=args.position,
                          font_size=args.font_size)
        bridge.show_sig.connect(overlay.show_text)
        bridge.hide_sig.connect(overlay.hide_text)
        state["overlay"] = overlay
        state["bridge"] = bridge
        state["pipe"] = Pipeline(reg, reg_dpr, args, log, bridge=bridge)
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
        top = int(g.height() * args.band)
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


if __name__ == "__main__":
    sys.exit(main())
