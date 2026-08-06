"""Always-on-top, click-through translated-subtitle overlay (AppKit).

Properties that make it behave:
  - NSWindowSharingNone     -> invisible to screen capture (no OCR feedback loop)
  - ScreenSaver window level + FullScreenAuxiliary -> shows over fullscreen video
  - ignoresMouseEvents      -> clicks pass through to the player underneath
  - AppKit native text      -> correct shaping for any script (Arabic
    ligatures, CJK, Indic) and per-language RTL/LTR direction

The target language decides paragraph direction (RTL for Arabic/Hebrew
scripts) and the font (SF Arabic for Arabic script; the system font
cascades correctly for everything else).

The translation anchors dynamically to the detected English text (an
anchor rect in CG top-left coords), with light smoothing so it doesn't
jitter. All methods must be called on the main thread
(AppHelper.callAfter).
"""

import AppKit
import Foundation

from ... import langs

# NSWindowCollectionBehavior flags
_CAN_JOIN_ALL_SPACES = 1 << 0
_STATIONARY = 1 << 4
_FULLSCREEN_AUX = 1 << 8

_SMOOTH_Y = 26.0   # ignore anchor moves smaller than this (points)
_SMOOTH_X = 60.0


def primary_screen_height() -> float:
    return AppKit.NSScreen.screens()[0].frame().size.height


def cg_to_appkit_y(cg_y: float, h: float) -> float:
    return primary_screen_height() - cg_y - h


def _nscolor(hex_str: str):
    h = (hex_str or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, IndexError):
        r = g = b = 1.0
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)


class Overlay:
    def __init__(
        self,
        region: tuple[float, float, float, float],
        position: str = "above",   # above | below | cover
        font_size: float = 46.0,
        gap: float = 6.0,
        text_color: str = "#FFFFFF",
        lang: str = langs.DEFAULT_TARGET,
    ):
        self.region = region
        self.position = position
        self.font_size = font_size
        self.gap = gap
        self.text_color = _nscolor(text_color)
        self.lang = lang
        self._rtl = langs.is_rtl(lang)
        self._last_text = None
        self._anchor = None  # smoothed anchor rect (CG coords)

        rect = Foundation.NSMakeRect(0, 0, 400, 60)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        w = self.window
        w.setOpaque_(False)
        w.setHasShadow_(False)
        w.setIgnoresMouseEvents_(True)
        # Load-bearing: without this the capture loop sees our own text
        # and cover mode feeds back (OCR reads the overlay, hides, reads
        # the English, shows again — measured ~13 flaps/line). ReadOnly
        # is the NSWindow default, so this must be explicit.
        w.setSharingType_(AppKit.NSWindowSharingNone)
        w.setLevel_(AppKit.NSScreenSaverWindowLevel)
        w.setCollectionBehavior_(
            _CAN_JOIN_ALL_SPACES | _STATIONARY | _FULLSCREEN_AUX
        )
        if position == "cover":
            w.setBackgroundColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.85)
            )
        else:
            w.setBackgroundColor_(AppKit.NSColor.clearColor())

        self.label = AppKit.NSTextField.alloc().initWithFrame_(rect)
        lbl = self.label
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setUsesSingleLineMode_(False)
        lbl.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        try:
            lbl.setMaximumNumberOfLines_(0)
        except AttributeError:
            pass
        w.contentView().addSubview_(lbl)

        if langs.script(lang) == "Arab":
            base = (
                AppKit.NSFont.fontWithName_size_("SF Arabic", font_size)
                or AppKit.NSFont.fontWithName_size_("Geeza Pro", font_size)
                or AppKit.NSFont.boldSystemFontOfSize_(font_size)
            )
        else:
            # the system font's fallback cascade covers every other
            # script (Latin, Cyrillic, CJK, Indic, Hebrew, ...)
            base = AppKit.NSFont.boldSystemFontOfSize_(font_size)
        fm = AppKit.NSFontManager.sharedFontManager()
        self.font = fm.convertFont_toHaveTrait_(base, AppKit.NSBoldFontMask) or base
        self._visible = False

    def _attributed(self, text: str):
        style = AppKit.NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(AppKit.NSTextAlignmentCenter)
        style.setBaseWritingDirection_(
            AppKit.NSWritingDirectionRightToLeft if self._rtl
            else AppKit.NSWritingDirectionLeftToRight)
        shadow = AppKit.NSShadow.alloc().init()
        shadow.setShadowColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.9)
        )
        shadow.setShadowOffset_(Foundation.NSMakeSize(0, -2))
        shadow.setShadowBlurRadius_(4.0)
        attrs = {
            AppKit.NSFontAttributeName: self.font,
            AppKit.NSForegroundColorAttributeName: self.text_color,
            AppKit.NSParagraphStyleAttributeName: style,
            AppKit.NSShadowAttributeName: shadow,
            # negative stroke = fill + outline (subtitle-style black edge)
            AppKit.NSStrokeWidthAttributeName: -3.0,
            AppKit.NSStrokeColorAttributeName: AppKit.NSColor.blackColor(),
        }
        return Foundation.NSAttributedString.alloc().initWithString_attributes_(
            text, attrs
        )

    def _smoothed(self, anchor):
        """Reuse the previous anchor for sub-jitter movements."""
        if anchor is None:
            return self._anchor or self.region
        if self._anchor is not None:
            ax, ay, aw, ah = self._anchor
            nx, ny, nw, nh = anchor
            if (abs(ny - ay) < _SMOOTH_Y and abs(ny + nh - (ay + ah)) < _SMOOTH_Y
                    and abs((nx + nw / 2) - (ax + aw / 2)) < _SMOOTH_X):
                return self._anchor
        self._anchor = anchor
        return anchor

    def set_text_color(self, hex_str: str):
        """Live-update the subtitle colour without restarting."""
        self.text_color = _nscolor(hex_str)
        if self._visible and self._last_text:
            self.label.setAttributedStringValue_(
                self._attributed(self._last_text))

    def show_text(self, text: str, anchor=None):
        self._last_text = text
        rx, ry, rw, rh = self._smoothed(anchor)
        astr = self._attributed(text)
        self.label.setAttributedStringValue_(astr)

        max_w = max(min(rw * 1.6, self.region[2]), 320.0)
        size = self.label.sizeThatFits_(Foundation.NSMakeSize(max_w, 10000))
        tw = min(max_w, size.width + 20)
        th = size.height + 10

        if self.position == "cover":
            pad = 8.0
            win_w, win_h = rw + 2 * pad, rh + 2 * pad
            win_x = rx - pad
            win_y = cg_to_appkit_y(ry - pad, win_h)
            lx = (win_w - tw) / 2.0
            ly = (win_h - th) / 2.0
        else:
            win_w, win_h = tw, th
            win_x = rx + (rw - tw) / 2.0
            if self.position == "below":
                cg_top = ry + rh + self.gap
            else:  # above
                cg_top = ry - th - self.gap
            win_y = cg_to_appkit_y(cg_top, th)
            lx, ly = 0.0, 0.0

        self.label.setFrame_(Foundation.NSMakeRect(lx, ly, tw, th))
        self.window.setFrame_display_(
            Foundation.NSMakeRect(win_x, win_y, win_w, win_h), True
        )
        # Re-assert on every show, not just the first: if the user enters
        # true full screen the video gets its own Space, and a window that
        # was only ordered front once stays stranded on the old Space.
        self.window.setCollectionBehavior_(
            _CAN_JOIN_ALL_SPACES | _STATIONARY | _FULLSCREEN_AUX)
        self.window.orderFrontRegardless()
        self._visible = True

    def hide(self):
        if self._visible:
            self.window.orderOut_(None)
            self._visible = False
        self._anchor = None
