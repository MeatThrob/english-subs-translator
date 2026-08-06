"""Cross-platform screen-region capture via mss (BitBlt on Windows,
CoreGraphics on macOS). Returns raw BGRA bytes + pixel dimensions.

Coordinate contract:
  - The region is stored in *logical* (DPI-independent) coordinates plus
    the devicePixelRatio of the screen it was picked on.
  - mss wants logical points on macOS but *physical pixels* on Windows,
    so the conversion happens here and nowhere else.
"""

import sys

import mss


class Capture:
    def __init__(self, region_logical, dpr: float = 1.0):
        x, y, w, h = region_logical
        if sys.platform == "win32":
            x, y, w, h = (round(v * dpr) for v in (x, y, w, h))
        self.box = {
            "left": int(x), "top": int(y),
            "width": int(w), "height": int(h),
        }
        self._sct = None  # created lazily in the calling thread

    def grab(self):
        """Return (bgra_bytes, width_px, height_px) or None on failure."""
        if self._sct is None:
            self._sct = mss.mss()
        try:
            shot = self._sct.grab(self.box)
        except Exception:
            return None
        return bytes(shot.raw), shot.width, shot.height

    def close(self):
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
