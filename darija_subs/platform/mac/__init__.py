"""macOS platform: Quartz capture, Apple Vision OCR, AppKit overlay/GUI.

This is the verified platform — the pipeline backend here must keep
behaviour identical to the pre-refactor macOS tree (regress_darija
46/46, e2e with the flap guard, GUI self-test)."""

DEFAULT_FONT_SIZE = 46.0
SETUP_HINT = "tools/setup_model.sh"


def check_permission():
    from . import capture
    if capture.has_screen_permission():
        return None
    capture.request_screen_permission()
    return ("ERROR: Screen Recording permission is missing.\n"
            "System Settings > Privacy & Security > Screen Recording ->\n"
            "enable it for your terminal app, then rerun.")


def display_bounds():
    import Quartz
    err, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
    if err or not ids:
        return None
    out = []
    for did in ids:
        b = Quartz.CGDisplayBounds(did)
        out.append((b.origin.x, b.origin.y, b.size.width, b.size.height))
    return out


def auto_region(band):
    import Quartz
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    top = b.size.height * band
    return (b.origin.x, b.origin.y + top, b.size.width, b.size.height - top)


class Backend:
    """Pipeline backend over Quartz capture + Vision OCR (see
    pipeline.py for the contract)."""

    name = "Vision"
    dpr = None
    capture_failed_info = {
        "hint": "grant Screen Recording permission and restart"}

    def __init__(self, args, region, dpr=None):
        self.region = tuple(region)

    def open(self):
        from . import capture, ocr   # Vision import deferred to the worker
        self._capture = capture
        self._ocr = ocr

    def grab(self):
        return self._capture.grab_region(*self.region)

    def ocr(self, frame, fast=False):
        return self._ocr.pixel_lines(frame, fast=fast)

    def pixels(self, frame):
        return self._capture.cgimage_pixels(frame)

    def close(self):
        pass


def make_backend(args, region, dpr=None):
    return Backend(args, region, dpr)


def run_gui(args, cfg, region, dpr, log, region_from_config=False):
    from .app import run_gui as _run
    return _run(args, cfg, region, log)


def gui_main(argv=None):
    from .gui import main as _main
    return _main(argv)
