"""Subtitle Translator — desktop control panel (native AppKit).

One big On/Off control, a searchable target-language picker (202
languages), the subtitle text size, and a colour wheel with a hex bar.
The translation model loads once at launch and is reused, so toggling
On is instant. Changing language reuses the already-loaded NLLB model
via retarget() — only crossing the Terjman/NLLB boundary loads weights.

The panel sets NSWindowSharingNone on itself, exactly like the overlay,
so the OCR can never read the panel's own text as if it were subtitles.

Run:  ./gui.sh          (or: .venv/bin/python -m darija_subs.gui)
"""

import colorsys
import math
import os
import sys
import threading
import time

import AppKit
import Foundation
import numpy as np
import objc
import Quartz
from PyObjCTools import AppHelper

from . import langs
from .main import (CACHE_DIR, CONFIG_PATH, MODELS_DIR, EventLog, Pipeline,
                   finalize_args, load_config, parse_args, save_config,
                   saved_region)
from .overlay import Overlay
from .translate import Translator

W, H = 460, 622
CARD_X, CARD_W = 24, 412
WHEEL = 168

ACCENT_ON = (0.16, 0.72, 0.36)     # green — click to turn on
ACCENT_OFF = (0.85, 0.27, 0.25)    # red   — click to turn off
ACCENT_BUSY = (0.45, 0.47, 0.52)   # grey  — loading


# ---------------------------------------------------------------- colour
def hex_to_rgb(s):
    h = (s or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, int(round(c * 255)))) for c in rgb)


def _color(rgb, a=1.0):
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*rgb, a)


def wheel_rgb_at(size, x, y, y_down=False):
    """Pure geometry: which colour lives at (x, y) on a `size` disc.
    Points outside the disc clamp to the rim."""
    c = size / 2.0
    dx = (x - c) / c
    dy = (y - c) / c
    if y_down:
        dy = -dy
    r = math.hypot(dx, dy)
    if r > 1.0:
        dx, dy, r = dx / r, dy / r, 1.0
    h = (math.degrees(math.atan2(dy, dx)) % 360.0) / 360.0
    return colorsys.hsv_to_rgb(h, min(1.0, r), 1.0)


def _wheel_image(size):
    """HSV disc: hue around, saturation outward, full value."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    c = (size - 1) / 2.0
    dx = (xx - c) / c
    dy = (yy - c) / c
    r = np.sqrt(dx * dx + dy * dy)
    h = (np.degrees(np.arctan2(-dy, dx)) % 360.0) / 360.0
    s = np.clip(r, 0.0, 1.0)
    v = np.ones_like(s)

    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    idx = (i % 6).astype(int)
    conds = [idx == k for k in range(6)]
    rr = np.select(conds, [v, q, p, p, t, v])
    gg = np.select(conds, [t, v, v, q, p, p])
    bb = np.select(conds, [p, p, t, v, v, q])

    edge = np.clip((1.0 - r) * c, 0.0, 1.0)   # 1px feather, no jaggies
    a = np.where(r <= 1.0, edge, 0.0)
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = (rr * a * 255).astype(np.uint8)
    rgba[..., 1] = (gg * a * 255).astype(np.uint8)
    rgba[..., 2] = (bb * a * 255).astype(np.uint8)
    rgba[..., 3] = (a * 255).astype(np.uint8)

    data = rgba.tobytes()
    provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    cg = Quartz.CGImageCreate(
        size, size, 8, 32, size * 4, cs,
        Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big,
        provider, None, False, Quartz.kCGRenderingIntentDefault)
    return AppKit.NSImage.alloc().initWithCGImage_size_(
        cg, Foundation.NSMakeSize(size, size))


class ColorWheel(AppKit.NSView):
    """Click or drag anywhere on the disc to pick a colour."""

    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(ColorWheel, self).initWithFrame_(frame)
        if self is None:
            return None
        self._cb = callback
        self._img = _wheel_image(int(frame.size.width))
        self._rgb = (1.0, 1.0, 1.0)
        return self

    @objc.python_method
    def set_rgb(self, rgb):
        self._rgb = rgb
        self.setNeedsDisplay_(True)

    @objc.python_method
    def pick_at(self, p):
        rgb = wheel_rgb_at(self.bounds().size.width, p.x, p.y)
        self._rgb = rgb
        self.setNeedsDisplay_(True)
        if self._cb:
            self._cb(rgb)

    def mouseDown_(self, event):
        self.pick_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    def mouseDragged_(self, event):
        self.pick_at(self.convertPoint_fromView_(event.locationInWindow(), None))

    def drawRect_(self, dirty):
        b = self.bounds()
        self._img.drawInRect_fromRect_operation_fraction_(
            b, Foundation.NSZeroRect,
            AppKit.NSCompositingOperationSourceOver, 1.0)
        h, s, _v = colorsys.rgb_to_hsv(*self._rgb)
        c = b.size.width / 2.0
        ang = h * 2 * math.pi
        px = c + math.cos(ang) * s * c
        py = c + math.sin(ang) * s * c
        outer = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            Foundation.NSMakeRect(px - 8.5, py - 8.5, 17, 17))
        outer.setLineWidth_(1.0)
        _color((0, 0, 0), 0.55).setStroke()
        outer.stroke()
        ring = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            Foundation.NSMakeRect(px - 7, py - 7, 14, 14))
        ring.setLineWidth_(2.5)
        AppKit.NSColor.whiteColor().setStroke()
        ring.stroke()


# ---------------------------------------------------------------- widgets
def _label(text, size, frame, *, bold=False, color=None, align=None,
           mono=False):
    f = AppKit.NSTextField.alloc().initWithFrame_(frame)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    if mono:
        font = AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
            size, AppKit.NSFontWeightRegular)
    elif bold:
        font = AppKit.NSFont.boldSystemFontOfSize_(size)
    else:
        font = AppKit.NSFont.systemFontOfSize_(size)
    f.setFont_(font)
    f.setStringValue_(text)
    if color is not None:
        f.setTextColor_(color)
    if align is not None:
        f.setAlignment_(align)
    f.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    return f


def _card(frame):
    v = AppKit.NSView.alloc().initWithFrame_(frame)
    v.setWantsLayer_(True)
    layer = v.layer()
    layer.setCornerRadius_(12.0)
    layer.setBackgroundColor_(_color((1, 1, 1), 0.06).CGColor())
    layer.setBorderWidth_(1.0)
    layer.setBorderColor_(_color((1, 1, 1), 0.08).CGColor())
    return v


class AccentButton(AppKit.NSButton):
    """Flat, filled, rounded button — the main On/Off control."""

    def initWithFrame_(self, frame):
        self = objc.super(AccentButton, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setBordered_(False)
        self.setButtonType_(AppKit.NSButtonTypeMomentaryChange)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(14.0)
        self.layer().setBackgroundColor_(_color(ACCENT_BUSY).CGColor())
        return self

    @objc.python_method
    def configure(self, title, rgb, enabled=True):
        self.layer().setBackgroundColor_(
            _color(rgb if enabled else ACCENT_BUSY).CGColor())
        style = AppKit.NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(AppKit.NSTextAlignmentCenter)
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                17, AppKit.NSFontWeightSemibold),
            AppKit.NSForegroundColorAttributeName:
                AppKit.NSColor.whiteColor() if enabled
                else _color((1, 1, 1), 0.55),
            AppKit.NSParagraphStyleAttributeName: style,
        }
        self.setAttributedTitle_(
            Foundation.NSAttributedString.alloc().initWithString_attributes_(
                title, attrs))
        self.setEnabled_(enabled)


# ---------------------------------------------------------------- panel
class Controller(AppKit.NSObject):

    def initWithArgs_cfg_(self, args, cfg):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.args = args
        self.cfg = cfg
        self.lang = args.lang
        self.translator = None
        self.translators = {}      # model_dir -> Translator (kept loaded)
        self.pipe = None
        self.overlay = None
        self.count = 0
        self.state = "loading"
        self.color_hex = self.cfg.get("text_color", "#FFFFFF")
        self._build_ui()
        self._load_model_async(restart=False)
        return self

    # ---------- ui ----------------------------------------------
    @objc.python_method
    def _build_ui(self):
        rect = Foundation.NSMakeRect(0, 0, W, H)
        style = (AppKit.NSWindowStyleMaskTitled
                 | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskMiniaturizable
                 | AppKit.NSWindowStyleMaskFullSizeContentView)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        win.setTitle_("Subtitle Translator")
        win.setTitlebarAppearsTransparent_(True)
        win.setTitleVisibility_(1)  # NSWindowTitleVisibilityHidden
        win.setMovableByWindowBackground_(True)
        # Keep the panel out of screen capture so the OCR can never read
        # the panel's own text as subtitles (same guarantee as the overlay).
        win.setSharingType_(AppKit.NSWindowSharingNone)
        win.setDelegate_(self)
        self.window = win

        bg = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        bg.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
        bg.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        bg.setState_(AppKit.NSVisualEffectStateActive)
        win.setContentView_(bg)
        root = bg

        secondary = _color((1, 1, 1), 0.55)
        faint = _color((1, 1, 1), 0.38)

        root.addSubview_(_label("Subtitle Translator", 26,
                                Foundation.NSMakeRect(0, 556, W, 34),
                                bold=True,
                                align=AppKit.NSTextAlignmentCenter))
        self.pair_label = _label(
            f"English  →  {langs.english_name(self.lang)}", 13,
            Foundation.NSMakeRect(0, 534, W, 20),
            color=secondary, align=AppKit.NSTextAlignmentCenter)
        root.addSubview_(self.pair_label)
        root.addSubview_(_label(
            "Runs entirely on this Mac · nothing is uploaded", 11,
            Foundation.NSMakeRect(0, 515, W, 16),
            color=faint, align=AppKit.NSTextAlignmentCenter))

        # status pill
        dot = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 5, 9, 9))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(4.5)
        self.dot = dot
        self.status = _label("Loading model…", 13,
                             Foundation.NSMakeRect(17, 0, 260, 18),
                             color=secondary)
        holder = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 0, 277, 20))
        holder.addSubview_(dot)
        holder.addSubview_(self.status)
        pill = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 474, W, 20))
        pill.addSubview_(holder)
        self._pill_holder = holder
        root.addSubview_(pill)

        # main button
        btn = AccentButton.alloc().initWithFrame_(
            Foundation.NSMakeRect((W - 220) / 2, 404, 220, 56))
        btn.setTarget_(self)
        btn.setAction_(objc.selector(self.toggle_, signature=b"v@:@"))
        btn.configure("Loading…", ACCENT_BUSY, enabled=False)
        self.button = btn
        root.addSubview_(btn)

        self.hint = _label("", 11, Foundation.NSMakeRect(0, 382, W, 16),
                           color=faint, align=AppKit.NSTextAlignmentCenter)
        root.addSubview_(self.hint)

        # ---- language card (type to search all 202 languages)
        lc = _card(Foundation.NSMakeRect(CARD_X, 320, CARD_W, 54))
        lc.addSubview_(_label("Language", 12,
                              Foundation.NSMakeRect(16, 17, 90, 18),
                              color=secondary))
        combo = AppKit.NSComboBox.alloc().initWithFrame_(
            Foundation.NSMakeRect(108, 13, CARD_W - 124, 27))
        combo.setUsesDataSource_(False)
        combo.addItemsWithObjectValues_(self._combo_items())
        combo.setCompletes_(True)          # type-ahead search
        combo.setNumberOfVisibleItems_(14)
        combo.setStringValue_(langs.label(self.lang))
        combo.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        combo.setTarget_(self)
        combo.setAction_(objc.selector(self.langChanged_, signature=b"v@:@"))
        self.lang_combo = combo
        lc.addSubview_(combo)
        root.addSubview_(lc)

        # ---- style card
        s = _card(Foundation.NSMakeRect(CARD_X, 26, CARD_W, 286))
        s.addSubview_(_label("SUBTITLE STYLE", 10,
                             Foundation.NSMakeRect(16, 262, 200, 14),
                             bold=True, color=faint))

        s.addSubview_(_label("Text size", 12,
                             Foundation.NSMakeRect(16, 230, 90, 18),
                             color=secondary))
        sl = AppKit.NSSlider.alloc().initWithFrame_(
            Foundation.NSMakeRect(108, 228, 240, 22))
        sl.setMinValue_(24)
        sl.setMaxValue_(80)
        sl.setDoubleValue_(float(self.cfg.get("font_size", 46.0)))
        sl.setContinuous_(True)
        sl.setTarget_(self)
        sl.setAction_(objc.selector(self.sizeChanged_, signature=b"v@:@"))
        self.size_slider = sl
        s.addSubview_(sl)
        self.size_value = _label(f"{int(sl.doubleValue())} pt", 12,
                                 Foundation.NSMakeRect(354, 230, 48, 18),
                                 mono=True, color=secondary)
        s.addSubview_(self.size_value)

        s.addSubview_(_label("Colour", 12,
                             Foundation.NSMakeRect(16, 196, 90, 18),
                             color=secondary))
        hexf = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(108, 192, 112, 24))
        hexf.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(
            12, AppKit.NSFontWeightRegular))
        hexf.setStringValue_(self.color_hex)
        hexf.setAlignment_(AppKit.NSTextAlignmentCenter)
        hexf.setTarget_(self)
        hexf.setAction_(objc.selector(self.hexChanged_, signature=b"v@:@"))
        self.hex_field = hexf
        s.addSubview_(hexf)

        sw = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(228, 192, 44, 24))
        sw.setWantsLayer_(True)
        sw.layer().setCornerRadius_(6.0)
        sw.layer().setBorderWidth_(1.0)
        sw.layer().setBorderColor_(_color((1, 1, 1), 0.25).CGColor())
        self.swatch = sw
        s.addSubview_(sw)

        for text, hx, x in (("White", "#FFFFFF", 282),
                            ("Yellow", "#FFD400", 340)):
            b = AppKit.NSButton.alloc().initWithFrame_(
                Foundation.NSMakeRect(x, 191, 56, 26))
            b.setTitle_(text)
            b.setBezelStyle_(AppKit.NSBezelStyleRounded)
            b.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            b.setTarget_(self)
            b.setAction_(objc.selector(self.presetChanged_, signature=b"v@:@"))
            b.setIdentifier_(hx)
            s.addSubview_(b)

        wheel = ColorWheel.alloc().initWithFrame_callback_(
            Foundation.NSMakeRect((CARD_W - WHEEL) / 2, 14, WHEEL, WHEEL),
            self._wheel_picked)
        self.wheel = wheel
        s.addSubview_(wheel)
        root.addSubview_(s)

        self._apply_color(self.color_hex, save=False)
        self._center_pill()
        win.center()
        win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _center_pill(self):
        text_w = self.status.attributedStringValue().size().width
        total = 17 + text_w
        self._pill_holder.setFrame_(
            Foundation.NSMakeRect((W - total) / 2.0, 0, total, 20))
        self.status.setFrame_(Foundation.NSMakeRect(17, 0, text_w + 4, 18))

    # ---------- colour -------------------------------------------
    @objc.python_method
    def _apply_color(self, hex_str, from_wheel=False, save=True):
        rgb = hex_to_rgb(hex_str)
        if rgb is None:                       # reject bad input, keep current
            self.hex_field.setStringValue_(self.color_hex)
            return
        self.color_hex = rgb_to_hex(rgb)
        self.hex_field.setStringValue_(self.color_hex)
        self.swatch.layer().setBackgroundColor_(_color(rgb).CGColor())
        if not from_wheel:
            self.wheel.set_rgb(rgb)
        if save:
            self.cfg["text_color"] = self.color_hex
            save_config(self.cfg)
        if self.overlay is not None:
            self.overlay.set_text_color(self.color_hex)

    @objc.python_method
    def _wheel_picked(self, rgb):
        self._apply_color(rgb_to_hex(rgb), from_wheel=True)

    def hexChanged_(self, sender):
        self._apply_color(sender.stringValue())

    def presetChanged_(self, sender):
        self._apply_color(str(sender.identifier()))

    def sizeChanged_(self, sender):
        v = float(int(sender.doubleValue()))
        self.size_value.setStringValue_(f"{int(v)} pt")
        self.cfg["font_size"] = v
        save_config(self.cfg)

    # ---------- language -----------------------------------------
    @objc.python_method
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

    def langChanged_(self, sender):
        code = langs.resolve(str(sender.stringValue()))
        if code is None:
            sender.setStringValue_(langs.label(self.lang))  # reject input
            return
        sender.setStringValue_(langs.label(code))
        if code == self.lang or self.state == "loading":
            return
        self.lang = code
        self.args.lang = code
        self.cfg["target_lang"] = code
        rec = [code] + [c for c in self.cfg.get("recent_langs", [])
                        if c != code]
        self.cfg["recent_langs"] = rec[:5]
        save_config(self.cfg)
        sender.removeAllItems()
        sender.addItemsWithObjectValues_(self._combo_items())
        sender.setStringValue_(langs.label(code))
        self.pair_label.setStringValue_(
            f"English  →  {langs.english_name(code)}")
        was_on = self.state == "on"
        if was_on:
            self._stop()
        self.state = "loading"
        self.button.configure("Loading…", ACCENT_BUSY, enabled=False)
        self._set_status("Loading model…", ACCENT_BUSY)
        self._load_model_async(restart=was_on)

    # ---------- model --------------------------------------------
    @objc.python_method
    def _model_dir(self) -> str:
        # an explicit --model always wins; otherwise route per language
        if getattr(self.args, "model_explicit", False):
            return self.args.model
        return langs.pick_model(self.lang, MODELS_DIR)

    @objc.python_method
    def _load_model_async(self, restart: bool):
        """Load (or retarget) the model for self.lang off the main thread.
        NLLB is one shared instance across all its languages, so switching
        between two NLLB languages is a cache-file swap, not a 600 MB load."""
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
                AppHelper.callAfter(self._model_failed, str(e))
                return
            AppHelper.callAfter(self._model_ready, model_dir, tr, restart)
        threading.Thread(target=work, daemon=True).start()

    @objc.python_method
    def _model_ready(self, model_dir, tr, restart: bool):
        self.translators[model_dir] = tr
        self.translator = tr
        self.args.model = model_dir
        self._set_state("off")
        if restart:
            self._start()

    @objc.python_method
    def _model_failed(self, err):
        self._set_status("Model failed to load", ACCENT_OFF)
        self.hint.setStringValue_(err[:90])
        self.button.configure("Unavailable", ACCENT_BUSY, enabled=False)

    # ---------- state --------------------------------------------
    @objc.python_method
    def _set_status(self, text, rgb):
        self.status.setStringValue_(text)
        self.dot.layer().setBackgroundColor_(_color(rgb).CGColor())
        self._center_pill()

    @objc.python_method
    def _set_state(self, state):
        self.state = state
        if state == "off":
            self._set_status("Stopped", ACCENT_OFF)
            self.button.configure("Off", ACCENT_OFF)
            self.hint.setStringValue_("Press the button to start translating")
        else:
            self._set_status("Watching for subtitles", ACCENT_ON)
            self.button.configure("On", ACCENT_ON)
            self.hint.setStringValue_(
                "Scanning your saved subtitle region"
                if saved_region(self.cfg) is not None
                else "Scanning the lower part of the screen")

    # ---------- pipeline -----------------------------------------
    @objc.python_method
    def _region(self):
        """Same precedence as run.sh: a saved/picked region from
        config.json wins; lower half of the main display otherwise."""
        reg = saved_region(self.cfg)
        if reg is not None:
            return reg
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        top = b.size.height * 0.5
        return (b.origin.x, b.origin.y + top,
                b.size.width, b.size.height - top)

    def toggle_(self, sender):
        if self.state == "off":
            self._start()
        elif self.state == "on":
            self._stop()

    @objc.python_method
    def _start(self):
        if self.translator is None or self.pipe is not None:
            return
        region = self._region()
        a = self.args
        a.position = "above"
        a.font_size = float(self.cfg.get("font_size", 46.0))

        self.overlay = Overlay(region, position="above",
                               font_size=a.font_size,
                               text_color=self.color_hex,
                               lang=self.lang)
        log = EventLog(a.log, echo=False, listener=self._on_event)
        self.pipe = Pipeline(region, a, log, overlay=self.overlay,
                             translator=self.translator)
        self.count = 0
        self.pipe.start()
        self._set_state("on")

    @objc.python_method
    def _stop(self):
        if self.pipe is not None:
            self.pipe.stop_flag.set()
            self.pipe.join(timeout=3)
            self.pipe = None
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay = None
        if self.translator is not None:
            self.translator.save_cache()
        self._set_state("off")

    @objc.python_method
    def _on_event(self, rec):
        AppHelper.callAfter(self._apply_event, rec)

    @objc.python_method
    def _apply_event(self, rec):
        ev = rec.get("event")
        if ev == "show":
            self.count += 1
        elif ev == "capture_failed":
            self._set_status("Screen Recording permission needed", ACCENT_OFF)

    def windowWillClose_(self, note):
        self._stop()
        AppKit.NSApp.terminate_(None)


def _install_menu():
    menubar = AppKit.NSMenu.alloc().init()
    item = AppKit.NSMenuItem.alloc().init()
    menubar.addItem_(item)
    AppKit.NSApp.setMainMenu_(menubar)
    app_menu = AppKit.NSMenu.alloc().init()
    app_menu.addItem_(
        AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Subtitle Translator", "terminate:", "q"))
    item.setSubmenu_(app_menu)


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

    app = AppKit.NSApplication.sharedApplication()
    # MUST be Accessory: under Regular policy macOS refuses to draw the
    # borderless overlay window, so no subtitle ever reaches the screen even
    # though capture/OCR/translation all succeed. Verified by A/B test.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _install_menu()
    ctl = Controller.alloc().initWithArgs_cfg_(args, cfg)
    app.activateIgnoringOtherApps_(True)

    if start_on:
        def _autostart():
            if ctl.translator is None:
                AppHelper.callLater(0.5, _autostart)
            else:
                ctl.toggle_(None)
        AppHelper.callLater(0.5, _autostart)
    if self_test:
        # The self-test drives the real widgets, and those handlers save to
        # config.json on purpose (colour, language, recents). Snapshot it
        # now so the run cannot leave the user's settings changed.
        threading.Thread(target=_self_test, args=(ctl, _config_snapshot()),
                         daemon=True).start()

    AppHelper.runEventLoop(installInterrupt=True)
    return 0


def _config_snapshot():
    """Return a callable that puts config.json back byte-for-byte."""
    try:
        with open(CONFIG_PATH, "rb") as f:
            data = f.read()
    except OSError:
        data = None

    def restore():
        if data is None:
            if os.path.exists(CONFIG_PATH):
                os.remove(CONFIG_PATH)
        else:
            with open(CONFIG_PATH, "wb") as f:
                f.write(data)
    return restore


def _self_test(ctl, restore=None):
    """Drive the panel programmatically and report.

    Never leaves the user's real config.json altered: the widget handlers
    under test save colour/language/recents to it by design.
    """
    try:
        _run_self_test(ctl)
    finally:
        if restore is not None:
            restore()
        AppHelper.callAfter(AppKit.NSApp.terminate_, None)


def _run_self_test(ctl):
    ok = True
    t0 = time.time()
    while ctl.translator is None and time.time() - t0 < 180:
        time.sleep(0.5)
    if ctl.translator is None:
        print("FAIL: model never loaded", flush=True)
        return
    print(f"model loaded in {time.time()-t0:.1f}s; state={ctl.state}",
          flush=True)

    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(6)
    label = str(ctl.button.attributedTitle().string())
    if ctl.pipe is None or not ctl.pipe.is_alive() or ctl.state != "on":
        print(f"FAIL: not running after On (state={ctl.state})", flush=True)
        ok = False
    elif label != "On":
        print(f"FAIL: running button should read 'On', reads {label!r}",
              flush=True)
        ok = False
    else:
        print(f"ON  ok — pipeline alive, overlay={ctl.overlay is not None}, "
              f"button reads 'On' (green), lines={ctl.count}", flush=True)

    AppHelper.callAfter(ctl._apply_color, "#FFD400")
    time.sleep(0.6)
    if ctl.color_hex != "#FFD400":
        print(f"FAIL: hex bar did not apply ({ctl.color_hex})", flush=True)
        ok = False
    else:
        print("colour ok — hex #FFD400 applied to the live overlay",
              flush=True)

    checks = [((WHEEL + 40, WHEEL / 2), "#FF0000", "3 o'clock"),
              ((-40, WHEEL / 2), "#00FFFF", "9 o'clock"),
              ((WHEEL / 2, WHEEL / 2), "#FFFFFF", "centre")]
    geom_ok = True
    for (x, y), want, where in checks:
        got = rgb_to_hex(wheel_rgb_at(WHEEL, x, y))
        if got != want:
            print(f"FAIL: wheel {where} = {got}, want {want}", flush=True)
            geom_ok = ok = False
    if geom_ok:
        print("wheel geometry ok — rim hues + white centre", flush=True)

    AppHelper.callAfter(ctl._wheel_picked, (0.0, 1.0, 1.0))
    time.sleep(0.6)
    if ctl.color_hex != "#00FFFF":
        print(f"FAIL: wheel pick did not sync ({ctl.color_hex})", flush=True)
        ok = False
    else:
        print("wheel ok — pick synced to the hex bar (#00FFFF)", flush=True)

    AppHelper.callAfter(ctl._apply_color, "not-a-colour")
    time.sleep(0.4)
    if ctl.color_hex != "#00FFFF":
        print("FAIL: bad hex was not rejected", flush=True)
        ok = False
    else:
        print("bad hex rejected, previous colour kept", flush=True)

    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(2)
    label = str(ctl.button.attributedTitle().string())
    if ctl.pipe is not None or ctl.state != "off":
        print(f"FAIL: still running after Off (state={ctl.state})", flush=True)
        ok = False
    elif label != "Off":
        print(f"FAIL: stopped button should read 'Off', reads {label!r}",
              flush=True)
        ok = False
    else:
        print("OFF ok — pipeline stopped, button reads 'Off' (red)",
              flush=True)

    t1 = time.time()
    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(1.5)
    if ctl.pipe is None or ctl.state != "on":
        print("FAIL: could not restart", flush=True)
        ok = False
    else:
        print(f"restart ok in {time.time()-t1:.1f}s (model reused)", flush=True)
    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(1)

    # -- language switching, driven like a user: type a partial name
    # into the combo and confirm (2 gestures: type + Enter) ----------
    def switch_to(typed, want_code, want_state, timeout):
        def do():
            ctl.lang_combo.setStringValue_(typed)
            ctl.langChanged_(ctl.lang_combo)
        AppHelper.callAfter(do)
        t = time.time()
        while time.time() - t < timeout:
            if (ctl.state == want_state and ctl.lang == want_code
                    and ctl.translator is not None
                    and ctl.translator.target_lang == want_code
                    and (want_state == "off"
                         or (ctl.pipe is not None and ctl.pipe.is_alive()))):
                return time.time() - t
            time.sleep(0.3)
        return None

    from .main import load_config as _reload_cfg

    def lang_check(typed, want_code, want_state, timeout, expect):
        dt = switch_to(typed, want_code, want_state, timeout)
        if dt is None:
            print(f"FAIL: typing {typed!r} never reached "
                  f"{want_code}/{want_state}", flush=True)
            return False
        combo_now = str(ctl.lang_combo.stringValue())
        saved = _reload_cfg().get("target_lang")
        sample = ctl.translator.translate("Good morning, my friend.")
        if combo_now != langs.label(want_code):
            print(f"FAIL: combo shows {combo_now!r} after {typed!r}", flush=True)
            return False
        if saved != want_code:
            print(f"FAIL: config target_lang={saved!r}, want {want_code}", flush=True)
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
    ok &= lang_check("spa", "spa_Latn", "off", 180, latin)
    # informal name routes back to the terjman specialist
    ok &= lang_check("darija", "ary_Arab", "off", 60, arab)

    # realistic case: switching while the overlay is running mid-show —
    # must auto-stop, swap model, and auto-restart without help
    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(2)
    if ctl.state != "on":
        print("FAIL: could not turn on before live-switch test", flush=True)
        ok = False
    ok &= lang_check("esp", "spa_Latn", "on", 120, latin)      # native, partial
    ok &= lang_check("русский", "rus_Cyrl", "on", 60, cyr)     # native spelling
    ok &= lang_check("DARIJA", "ary_Arab", "on", 60, arab)     # case-insensitive

    # recents surface at the top of the dropdown, current first
    top3 = [str(ctl.lang_combo.itemObjectValueAtIndex_(i)) for i in range(3)]
    want_top = [langs.label(c) for c in ("ary_Arab", "rus_Cyrl", "spa_Latn")]
    if top3 != want_top:
        print(f"FAIL: recents order {top3} != {want_top}", flush=True)
        ok = False
    else:
        print(f"recents ok — dropdown starts {top3}", flush=True)

    AppHelper.callAfter(ctl.toggle_, None)
    time.sleep(1)

    print("PASS" if ok else "FAIL", flush=True)
    # terminate is the caller's job — it must happen after config restore


if __name__ == "__main__":
    sys.exit(main())
