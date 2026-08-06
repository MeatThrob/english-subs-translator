"""macOS CLI GUI glue: one NSApp; optionally the picker first, then
overlay + pipeline worker. This is the (previously inline) GUI section
of the old macOS main.py, unchanged in behaviour."""

from ...pipeline import Pipeline, save_config


class MacUI:
    """Marshals pipeline show/hide onto the AppKit main thread."""

    def __init__(self, overlay):
        self.overlay = overlay

    def show(self, text, anchor=None):
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self.overlay.show_text, text, anchor)

    def hide(self):
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self.overlay.hide)


def run_gui(args, cfg, region, log):
    import AppKit
    from PyObjCTools import AppHelper

    from . import Backend
    from .overlay import Overlay
    from .picker import make_picker

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    state = {"picker": None, "overlay": None, "pipe": None}

    def start_pipeline(reg):
        cfg["region"] = list(reg)
        cfg["position"] = args.position
        cfg["font_size"] = args.font_size
        cfg["target_lang"] = args.lang
        save_config(cfg)
        state["overlay"] = Overlay(
            reg, position=args.position, font_size=args.font_size,
            lang=args.lang,
        )
        state["pipe"] = Pipeline(reg, args, log, Backend(args, reg),
                                 ui=MacUI(state["overlay"]))
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
