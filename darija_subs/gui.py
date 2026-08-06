"""Subtitle Translator — desktop control panel (native AppKit).

One big On/Off control, the subtitle text size, and a colour wheel with
a hex bar. The translation model loads once at launch and is reused, so
toggling On is instant.

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

from .main import (DEFAULT_CACHE, EventLog, Pipeline, load_config, parse_args,
                   save_config)
from .overlay import Overlay
from .translate import Translator

W, H = 460, 566
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

    def initWithArgs_(self, args):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.args = args
        self.cfg = load_config()
        self.translator = None
        self.pipe = None
        self.overlay = None
        self.count = 0
        self.state = "loading"
        self.color_hex = self.cfg.get("text_color", "#FFFFFF")
        self._build_ui()
        self._load_model_async()
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
                                Foundation.NSMakeRect(0, 500, W, 34),
                                bold=True,
                                align=AppKit.NSTextAlignmentCenter))
        root.addSubview_(_label("English  →  Moroccan Darija", 13,
                                Foundation.NSMakeRect(0, 478, W, 20),
                                color=secondary,
                                align=AppKit.NSTextAlignmentCenter))
        root.addSubview_(_label(
            "Runs entirely on this Mac · nothing is uploaded", 11,
            Foundation.NSMakeRect(0, 459, W, 16),
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
            Foundation.NSMakeRect(0, 418, W, 20))
        pill.addSubview_(holder)
        self._pill_holder = holder
        root.addSubview_(pill)

        # main button
        btn = AccentButton.alloc().initWithFrame_(
            Foundation.NSMakeRect((W - 220) / 2, 348, 220, 56))
        btn.setTarget_(self)
        btn.setAction_(objc.selector(self.toggle_, signature=b"v@:@"))
        btn.configure("Loading…", ACCENT_BUSY, enabled=False)
        self.button = btn
        root.addSubview_(btn)

        self.hint = _label("", 11, Foundation.NSMakeRect(0, 326, W, 16),
                           color=faint, align=AppKit.NSTextAlignmentCenter)
        root.addSubview_(self.hint)

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

    # ---------- model --------------------------------------------
    @objc.python_method
    def _load_model_async(self):
        def work():
            try:
                tr = Translator(
                    self.args.model,
                    beam_size=self.args.beam,
                    cache_path=None if self.args.no_cache else DEFAULT_CACHE,
                )
                tr.warmup()
            except Exception as e:
                AppHelper.callAfter(self._model_failed, str(e))
                return
            AppHelper.callAfter(self._model_ready, tr)
        threading.Thread(target=work, daemon=True).start()

    @objc.python_method
    def _model_ready(self, tr):
        self.translator = tr
        self._set_state("off")

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
            self.hint.setStringValue_("Scanning the lower part of the screen")

    # ---------- pipeline -----------------------------------------
    @objc.python_method
    def _region(self):
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
                               text_color=self.color_hex)
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
    if not os.path.isdir(args.model):
        print(f"ERROR: model not found at {args.model}", file=sys.stderr)
        return 2

    app = AppKit.NSApplication.sharedApplication()
    # MUST be Accessory: under Regular policy macOS refuses to draw the
    # borderless overlay window, so no Arabic ever reaches the screen even
    # though capture/OCR/translation all succeed. Verified by A/B test.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _install_menu()
    ctl = Controller.alloc().initWithArgs_(args)
    app.activateIgnoringOtherApps_(True)

    if start_on:
        def _autostart():
            if ctl.translator is None:
                AppHelper.callLater(0.5, _autostart)
            else:
                ctl.toggle_(None)
        AppHelper.callLater(0.5, _autostart)
    if self_test:
        threading.Thread(target=_self_test, args=(ctl,), daemon=True).start()

    AppHelper.runEventLoop(installInterrupt=True)
    return 0


def _self_test(ctl):
    """Drive the panel programmatically and report."""
    ok = True
    t0 = time.time()
    while ctl.translator is None and time.time() - t0 < 180:
        time.sleep(0.5)
    if ctl.translator is None:
        print("FAIL: model never loaded", flush=True)
        AppHelper.callAfter(AppKit.NSApp.terminate_, None)
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

    print("PASS" if ok else "FAIL", flush=True)
    AppHelper.callAfter(AppKit.NSApp.terminate_, None)


if __name__ == "__main__":
    sys.exit(main())
