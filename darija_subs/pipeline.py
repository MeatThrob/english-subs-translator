"""Platform-independent core: paths, config, event log, and the
capture -> OCR -> filter -> translate -> overlay loop.

The loop was measured ~90% identical between the macOS and Windows
trees before it was factored out; it now exists once, here. Everything
platform-specific enters through a small backend object created by
``darija_subs.platform.impl.make_backend()``:

    backend.open()               called once, inside the worker thread
                                 (raises -> "fatal" event, exit 2)
    backend.grab()               -> opaque frame handle, or None
    backend.ocr(frame, fast=..)  -> [(text, conf, (x, y, w, h))] in image
                                    pixels, top-left origin, sorted top
                                    to bottom
    backend.pixels(frame)        -> (bgra_bytes, bytes_per_row, w, h)
    backend.capture_failed_info  dict merged into the capture_failed event
                                 (macOS adds the Screen Recording hint)
    backend.dpr                  devicePixelRatio (Windows) or None (macOS)
    backend.name                 OCR engine name for the ready event
    backend.close()

UI updates leave through ``ui.show(text, anchor)`` / ``ui.hide()``;
each platform supplies an object that marshals those onto its main
thread (AppHelper.callAfter on macOS, queued Qt signals on Windows).
"""

import json
import os
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
CACHE_DIR = os.path.join(PROJECT_ROOT, "cache")
LEGACY_CACHE = os.path.join(PROJECT_ROOT, "cache.json")

from . import subfilter, textnorm
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


def saved_region(cfg: dict) -> tuple[float, float, float, float] | None:
    """The capture region saved in config.json, if it is usable now.

    This is what makes `--pick` stick: the picked box is saved, and both
    the CLI and the control panel call this before falling back to the
    lower-half auto-scan. Returns None for a missing/malformed region or
    one that no longer overlaps any active display (e.g. the monitor it
    was picked on is unplugged) — silently capturing an off-screen rect
    would fail with a misleading permission hint. When the platform
    cannot enumerate displays (Windows CLI before Qt starts), the region
    is assumed usable.
    """
    reg = cfg.get("region")
    try:
        x, y, w, h = (float(v) for v in reg)
    except (TypeError, ValueError):
        return None
    if w < 50 or h < 20:
        return None
    from .platform import impl
    displays = impl.display_bounds()
    if displays is None:
        return (x, y, w, h)  # display list unavailable: assume usable
    for dx, dy, dw, dh in displays:
        if x < dx + dw and x + w > dx and y < dy + dh and y + h > dy:
            return (x, y, w, h)
    return None


def lines_to_text(lines) -> str:
    """Join OCR lines into one subtitle string."""
    return " ".join(t for t, _c, _b in lines).strip()


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
    """Capture -> OCR -> translate loop. UI updates hop to the platform's
    main thread through the `ui` bridge."""

    def __init__(self, region, args, log: EventLog, backend, ui=None,
                 translator=None):
        super().__init__(daemon=True)
        self.region = tuple(region)
        self.args = args
        self.log = log
        self.backend = backend
        self.ui = ui
        self.translator = translator   # preloaded model (GUI reuses it)
        self.stop_flag = threading.Event()

    def _show(self, translated: str, anchor=None):
        if self.ui is not None:
            self.ui.show(translated, anchor)

    def _hide(self):
        if self.ui is not None:
            self.ui.hide()

    def run(self):
        a = self.args
        backend = self.backend
        try:
            backend.open()
        except Exception as e:
            self.log.emit("fatal", error=f"OCR backend failed: {e}")
            os._exit(2)
        translator = self.translator
        if translator is None:
            try:
                translator = Translator(
                    a.model,
                    target_lang=a.lang,
                    beam_size=a.beam,
                    cache_dir=None if a.no_cache else CACHE_DIR,
                )
                translator.warmup()
            except Exception as e:
                self.log.emit("fatal", error=f"translator load failed: {e}")
                os._exit(2)
        ready_kw = {} if backend.dpr is None else {"dpr": backend.dpr}
        self.log.emit("ready", model=a.model, lang=translator.target_lang,
                      ocr=backend.name, region=list(self.region), **ready_kw)

        committed = None
        empty_streak = 0
        grab_failures = 0
        interval = a.interval / 1000.0
        rx, ry, rw, rh = self.region

        while not self.stop_flag.is_set():
            t0 = time.perf_counter()
            frame = backend.grab()
            if frame is None:
                grab_failures += 1
                if grab_failures in (1, 50):
                    self.log.emit("capture_failed",
                                  **backend.capture_failed_info)
                time.sleep(0.5)
                continue
            grab_failures = 0

            t1 = time.perf_counter()
            try:
                lines = backend.ocr(frame, fast=a.fast_ocr)
            except Exception as e:
                self.log.emit("ocr_error", error=str(e))
                time.sleep(0.5)
                continue
            text = ""
            anchor = None
            if lines:   # pixel extraction only when there is text to vet
                buf, bpr, iw, ih = backend.pixels(frame)
                scale_x = iw / max(1.0, rw)
                scale_y = ih / max(1.0, rh)
                lines, ubox = subfilter.filter_lines(buf, iw, ih, lines,
                                                     scale_y, bpr=bpr)
                text = textnorm.normalize(lines_to_text(lines))
                if ubox is not None:
                    ax, ay, aw, ah = ubox
                    anchor = (
                        rx + ax / scale_x,
                        ry + ay / scale_y,
                        aw / scale_x,
                        ah / scale_y,
                    )
            ocr_ms = (time.perf_counter() - t1) * 1000

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
                    translated = translator.translate(text)
                    tr_ms = (time.perf_counter() - t2) * 1000
                    committed = text
                    if translated:
                        self._show(translated, anchor)
                        self.log.emit(
                            "show",
                            en=text,
                            ar=translated,   # key kept for log/tool compat
                            ocr_ms=round(ocr_ms, 1),
                            tr_ms=round(tr_ms, 1),
                        )

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, interval - elapsed))

        translator.save_cache()
        backend.close()
