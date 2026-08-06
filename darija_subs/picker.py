"""Full-screen region picker with an adjustable selection box.

Draw a box around the English subtitle area, then adjust it:
  - drag inside the box to move it
  - drag an edge or corner handle to resize it
  - drag anywhere outside to start a new box
  - Enter confirms, Esc cancels

Calls the callback with (x, y, w, h) in CG global top-left coordinates,
or None if cancelled. Nothing is committed until Enter.
"""

import AppKit
import Foundation
import objc

MIN_W, MIN_H = 150, 40
GRIP = 12  # px hit zone around edges/handles


class _PickerWindow(AppKit.NSWindow):
    def canBecomeKeyWindow(self):
        return True


class _PickerView(AppKit.NSView):
    def initWithFrame_callback_(self, frame, callback):
        self = objc.super(_PickerView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._callback = callback
        self._rect = None        # current selection (NSRect) or None
        self._mode = None        # 'new' | 'move' | 'resize'
        self._edges = (False, False, False, False)  # L, R, T, B (resize)
        self._anchor = None      # drag start point
        self._rect0 = None       # rect at drag start
        return self

    def acceptsFirstResponder(self):
        return True

    # ---- keys ------------------------------------------------------
    def keyDown_(self, event):
        code = event.keyCode()
        if code == 53:  # Esc
            self._callback(None)
        elif code in (36, 76):  # Return / keypad Enter
            r = self._rect
            if r is not None and r.size.width >= MIN_W and r.size.height >= MIN_H:
                screen_h = AppKit.NSScreen.screens()[0].frame().size.height
                cg_x = r.origin.x
                cg_y = screen_h - (r.origin.y + r.size.height)
                self._callback((cg_x, cg_y, r.size.width, r.size.height))

    # ---- mouse -----------------------------------------------------
    @objc.python_method
    def _hit(self, p):
        """Classify a mouse-down point against the current rect."""
        r = self._rect
        if r is None:
            return "new", (False, False, False, False)
        x0, y0 = r.origin.x, r.origin.y
        x1, y1 = x0 + r.size.width, y0 + r.size.height
        near_x = x0 - GRIP <= p.x <= x1 + GRIP
        near_y = y0 - GRIP <= p.y <= y1 + GRIP
        L = abs(p.x - x0) <= GRIP and near_y
        R = abs(p.x - x1) <= GRIP and near_y
        B = abs(p.y - y0) <= GRIP and near_x  # AppKit: y0 = bottom
        T = abs(p.y - y1) <= GRIP and near_x
        if L or R or T or B:
            return "resize", (L, R, T, B)
        if x0 < p.x < x1 and y0 < p.y < y1:
            return "move", (False, False, False, False)
        return "new", (False, False, False, False)

    def mouseDown_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._mode, self._edges = self._hit(p)
        self._anchor = p
        self._rect0 = self._rect
        if self._mode == "new":
            self._rect = Foundation.NSMakeRect(p.x, p.y, 0, 0)
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        a = self._anchor
        if self._mode == "new":
            self._rect = Foundation.NSMakeRect(
                min(a.x, p.x), min(a.y, p.y), abs(p.x - a.x), abs(p.y - a.y)
            )
        elif self._mode == "move" and self._rect0 is not None:
            b = self.bounds()
            r = self._rect0
            nx = r.origin.x + (p.x - a.x)
            ny = r.origin.y + (p.y - a.y)
            nx = max(0, min(nx, b.size.width - r.size.width))
            ny = max(0, min(ny, b.size.height - r.size.height))
            self._rect = Foundation.NSMakeRect(nx, ny, r.size.width, r.size.height)
        elif self._mode == "resize" and self._rect0 is not None:
            r = self._rect0
            L, R, T, B = self._edges
            x0, y0 = r.origin.x, r.origin.y
            x1, y1 = x0 + r.size.width, y0 + r.size.height
            if L:
                x0 = p.x
            if R:
                x1 = p.x
            if B:
                y0 = p.y
            if T:
                y1 = p.y
            self._rect = Foundation.NSMakeRect(
                min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
            )
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        # a fresh drag that stayed tiny was accidental — discard it,
        # but never discard an existing box being adjusted
        if self._mode == "new" and self._rect is not None:
            if self._rect.size.width < 30 or self._rect.size.height < 15:
                self._rect = None
        self._mode = None
        self.setNeedsDisplay_(True)

    # ---- drawing ---------------------------------------------------
    def drawRect_(self, dirty):
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.35).setFill()
        AppKit.NSBezierPath.fillRect_(self.bounds())
        r = self._rect
        if r is not None and r.size.width > 2 and r.size.height > 2:
            ctx = AppKit.NSGraphicsContext.currentContext()
            ctx.setCompositingOperation_(AppKit.NSCompositingOperationClear)
            AppKit.NSBezierPath.fillRect_(r)
            ctx.setCompositingOperation_(AppKit.NSCompositingOperationSourceOver)

            ok = r.size.width >= MIN_W and r.size.height >= MIN_H
            color = (AppKit.NSColor.systemGreenColor() if ok
                     else AppKit.NSColor.systemOrangeColor())
            color.setStroke()
            path = AppKit.NSBezierPath.bezierPathWithRect_(r)
            path.setLineWidth_(2.0)
            path.stroke()

            # 8 resize handles
            color.setFill()
            x0, y0 = r.origin.x, r.origin.y
            x1, y1 = x0 + r.size.width, y0 + r.size.height
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            for hx, hy in ((x0, y0), (xm, y0), (x1, y0), (x0, ym),
                           (x1, ym), (x0, y1), (xm, y1), (x1, y1)):
                AppKit.NSBezierPath.fillRect_(
                    Foundation.NSMakeRect(hx - 4, hy - 4, 8, 8)
                )

            status = (f"{int(r.size.width)} x {int(r.size.height)}"
                      + ("   —   press ENTER to start" if ok
                         else f"   (too small — need at least {MIN_W} x {MIN_H})"))
            self._draw_label(status, x0, y1 + 10)

        msg = ("Drag a box around the ENGLISH subtitles — move it, drag "
               "edges to resize — ENTER to start, Esc to cancel")
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(20),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        s = Foundation.NSAttributedString.alloc().initWithString_attributes_(msg, attrs)
        size = s.size()
        b = self.bounds()
        s.drawAtPoint_(
            Foundation.NSMakePoint(
                (b.size.width - size.width) / 2.0, b.size.height - 80
            )
        )

    @objc.python_method
    def _draw_label(self, text, x, y):
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(15),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        }
        s = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            text, attrs
        )
        b = self.bounds()
        if y + 22 > b.size.height:
            y = b.size.height - 22
        s.drawAtPoint_(Foundation.NSMakePoint(x, y))


def make_picker(callback):
    """Create and show the picker window. Returns the window (keep a ref)."""
    screen = AppKit.NSScreen.screens()[0]
    frame = screen.frame()
    win = _PickerWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame,
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    win.setOpaque_(False)
    win.setBackgroundColor_(AppKit.NSColor.clearColor())
    win.setLevel_(AppKit.NSScreenSaverWindowLevel)
    win.setSharingType_(AppKit.NSWindowSharingNone)
    view = _PickerView.alloc().initWithFrame_callback_(
        Foundation.NSMakeRect(0, 0, frame.size.width, frame.size.height), callback
    )
    win.setContentView_(view)
    win.makeFirstResponder_(view)
    win.makeKeyAndOrderFront_(None)
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    return win
