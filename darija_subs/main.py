"""darija-subs: live English-subtitle -> Moroccan Arabic overlay.

Pipeline (all local, no network at runtime):
  screen region capture (Quartz) -> Apple Vision OCR (English)
  -> NLLB int8 via CTranslate2 (eng_Latn -> ary_Arab) with disk cache
  -> click-through AppKit overlay that mirrors the English show/hide.

Usage:
  run.sh --pick          # first run: drag-select the subtitle area, then it starts
  run.sh                 # reuse saved region
  run.sh --headless      # no overlay window; prints/logs events (testing)
"""

import argparse
import json
import os
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
DEFAULT_MODEL = os.path.join(PROJECT_ROOT, "models", "terjman-nano-int8")
DEFAULT_CACHE = os.path.join(PROJECT_ROOT, "cache.json")

from . import capture, ocr, subfilter, textnorm
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
    """Capture -> OCR -> translate loop. UI updates hop to the main thread."""

    def __init__(self, region, args, log: EventLog, overlay=None,
                 translator=None):
        super().__init__(daemon=True)
        self.region = tuple(region)
        self.args = args
        self.log = log
        self.overlay = overlay
        self.translator = translator   # preloaded model (GUI reuses it)
        self.stop_flag = threading.Event()

    def _show(self, arabic: str, anchor=None):
        if self.overlay is not None:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self.overlay.show_text, arabic, anchor)

    def _hide(self):
        if self.overlay is not None:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self.overlay.hide)

    def run(self):
        a = self.args
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
        self.log.emit("ready", model=a.model, region=list(self.region))

        committed = None
        empty_streak = 0
        grab_failures = 0
        interval = a.interval / 1000.0

        while not self.stop_flag.is_set():
            t0 = time.perf_counter()
            img = capture.grab_region(*self.region)
            if img is None:
                grab_failures += 1
                if grab_failures in (1, 50):
                    self.log.emit(
                        "capture_failed",
                        hint="grant Screen Recording permission and restart",
                    )
                time.sleep(0.5)
                continue
            grab_failures = 0

            t1 = time.perf_counter()
            lines = ocr.ocr_cgimage(img, fast=a.fast_ocr)
            lines, ubox = subfilter.filter_lines(img, lines, self.region[3])
            text = textnorm.normalize(ocr.lines_to_text(lines))
            ocr_ms = (time.perf_counter() - t1) * 1000

            anchor = None
            if ubox is not None:
                rx, ry, rw, rh = self.region
                ux, uy, uw, uh = ubox
                anchor = (
                    rx + ux * rw,
                    ry + (1 - uy - uh) * rh,
                    uw * rw,
                    uh * rh,
                )

            if len(text) > 220:
                # not a subtitle — region is probably over the wrong thing
                text = ""
            if not text:
                empty_streak += 1
                if committed is not None and empty_streak >= a.hide_frames:
                    committed = None
                    self._hide()
                    self.log.emit("hide")
            else:
                empty_streak = 0
                if text != committed:
                    t2 = time.perf_counter()
                    arabic = translator.translate(text)
                    tr_ms = (time.perf_counter() - t2) * 1000
                    committed = text
                    if arabic:
                        self._show(arabic, anchor)
                        self.log.emit(
                            "show",
                            en=text,
                            ar=arabic,
                            ocr_ms=round(ocr_ms, 1),
                            tr_ms=round(tr_ms, 1),
                        )

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, interval - elapsed))

        translator.save_cache()


def parse_args(argv):
    p = argparse.ArgumentParser(prog="darija-subs", description=__doc__)
    p.add_argument("--pick", action="store_true",
                   help="manually drag-select a region instead of the "
                        "automatic lower-half scan")
    p.add_argument("--band", type=float, default=0.5,
                   help="auto-scan band: fraction of screen height where the "
                        "scan starts (default 0.5 = lower half)")
    p.add_argument("--region", type=str, help="x,y,w,h in global screen points (top-left origin)")
    p.add_argument("--position", choices=["above", "below", "cover"], default=None,
                   help="where the Arabic goes relative to the English (cover = hide the English)")
    p.add_argument("--font-size", type=float, default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--beam", type=int, default=2,
                   help="beam size (2 = best quality/speed tradeoff, measured)")
    p.add_argument("--interval", type=int, default=50, help="poll interval ms")
    p.add_argument("--hide-frames", type=int, default=2,
                   help="consecutive empty OCR reads before hiding the Arabic")
    p.add_argument("--turbo", action="store_true",
                   help="lowest latency: near-continuous OCR (~25ms interval) "
                        "and hide on the first empty read. Higher CPU use; "
                        "rare flicker possible on washed-out scenes")
    p.add_argument("--fast-ocr", action="store_true", help="Vision fast mode (lower accuracy)")
    p.add_argument("--headless", action="store_true", help="no overlay window; log events only")
    p.add_argument("--log", type=str, default=None, help="append JSONL events to this file")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--quiet", action="store_true", help="don't echo events to stdout")
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
        args.font_size = cfg.get("font_size", 46.0)

    if not os.path.isdir(args.model):
        print(
            f"ERROR: translation model not found at {args.model}\n"
            f"Run: tools/setup_model.sh",
            file=sys.stderr,
        )
        return 2

    if not capture.has_screen_permission():
        capture.request_screen_permission()
        print(
            "ERROR: Screen Recording permission is missing.\n"
            "System Settings > Privacy & Security > Screen Recording ->\n"
            "enable it for your terminal app, then rerun.",
            file=sys.stderr,
        )
        return 3

    region = None
    if args.region:
        region = tuple(float(v) for v in args.region.split(","))
    elif not args.pick:
        import Quartz
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        top = b.size.height * args.band
        region = (b.origin.x, b.origin.y + top,
                  b.size.width, b.size.height - top)

    log = EventLog(args.log, echo=not args.quiet)

    if args.headless:
        if region is None:
            print("ERROR: --headless needs --region or a saved config", file=sys.stderr)
            return 2
        pipe = Pipeline(region, args, log, overlay=None)
        pipe.run()  # blocking, Ctrl+C to stop
        return 0

    # GUI path: one NSApp; optionally picker first, then overlay + worker.
    import AppKit
    from PyObjCTools import AppHelper
    from .overlay import Overlay
    from .picker import make_picker

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    state = {"picker": None, "overlay": None, "pipe": None}

    def start_pipeline(reg):
        cfg["region"] = list(reg)
        cfg["position"] = args.position
        cfg["font_size"] = args.font_size
        save_config(cfg)
        state["overlay"] = Overlay(
            reg, position=args.position, font_size=args.font_size
        )
        state["pipe"] = Pipeline(reg, args, log, overlay=state["overlay"])
        state["pipe"].start()

    def on_picked(reg):
        w = state.pop("picker", None)
        if w is not None:
            w.orderOut_(None)
        if reg is None:
            log.emit("cancelled")
            AppHelper.stopEventLoop()
            return
        log.emit("region_selected", region=list(reg))
        start_pipeline(reg)

    if args.pick or region is None:
        state["picker"] = make_picker(on_picked)
    else:
        start_pipeline(region)

    try:
        AppHelper.runEventLoop(installInterrupt=True)
    except KeyboardInterrupt:
        pass
    if state["pipe"] is not None:
        state["pipe"].stop_flag.set()
        state["pipe"].join(timeout=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
