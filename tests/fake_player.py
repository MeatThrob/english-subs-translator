"""Fake anime player: a window that cycles timed English subtitle lines.

Usage: fake_player.py --rect X,Y,W,H --log PATH --trigger PATH
Rect is in CG global top-left coords. The player waits for the trigger
file to appear before starting its script, then logs every show/hide
with timestamps for the e2e test to compare against the app's log.
"""

import argparse
import json
import os
import sys
import threading
import time

import AppKit
import Foundation
from PyObjCTools import AppHelper

SCRIPT = [
    ("The moon is beautiful tonight, isn't it?", 3.5, 1.2),
    ("Run! The titans have breached the wall!", 3.0, 0.8),
    ("I made this bento just for you, senpai.", 3.5, 1.5),
    ("My sword technique has no equal in this world.", 3.0, 1.0),
    ("Why would you protect someone like me?", 3.0, 1.2),
    ("The moon is beautiful tonight, isn't it?", 2.5, 0.8),  # repeat: cache hit
    ("This is our final battle. Give it everything!", 3.5, 1.0),
    ("See you tomorrow at the academy.", 3.0, 1.0),
]


def cg_to_appkit_y(cg_y, h):
    return AppKit.NSScreen.screens()[0].frame().size.height - cg_y - h


class Player:
    def __init__(self, rect, log_path, trigger):
        self.trigger = trigger
        self.log = open(log_path, "a", encoding="utf-8")
        x, y, w, h = rect
        frame = Foundation.NSMakeRect(x, cg_to_appkit_y(y, h), w, h)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False,
        )
        self.window.setBackgroundColor_(AppKit.NSColor.blackColor())
        self.window.setLevel_(AppKit.NSFloatingWindowLevel)
        self.label = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 0, w, h)
        )
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(False)
        self.label.setEditable_(False)
        self.label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(38))
        self.label.setTextColor_(AppKit.NSColor.whiteColor())
        self.window.contentView().addSubview_(self.label)
        self.window.orderFrontRegardless()

    def emit(self, event, **kw):
        rec = {"t": time.time(), "event": event, **kw}
        self.log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.log.flush()

    def set_text(self, s):
        self.label.setStringValue_(s)
        self.window.display()

    def run_script(self):
        while not os.path.exists(self.trigger):
            time.sleep(0.1)
        time.sleep(0.5)
        for text, show_s, gap_s in SCRIPT:
            AppHelper.callAfter(self.set_text, text)
            self.emit("show", text=text)
            time.sleep(show_s)
            AppHelper.callAfter(self.set_text, "")
            self.emit("hide")
            time.sleep(gap_s)
        self.emit("done")
        AppHelper.callAfter(AppHelper.stopEventLoop)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rect", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--trigger", required=True)
    a = p.parse_args()
    rect = tuple(float(v) for v in a.rect.split(","))

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    player = Player(rect, a.log, a.trigger)
    threading.Thread(target=player.run_script, daemon=True).start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
