"""Unit test for ocr.downscale_bgra — the guard that keeps oversized
capture regions under WinRT's OcrEngine.MaxImageDimension (~2600 px).

Runs on any platform (pure pixels, no OCR engine). Checks:
  - buffers over the limit are shrunk to fit, aspect preserved
  - buffers under the limit pass through untouched
  - a bright feature's position survives the round trip through the
    same scale-back mapping recognize() applies to result boxes

Run: .venv\\Scripts\\python tests\\test_downscale.py   (or bin/python)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from darija_subs.ocr import downscale_bgra

FAILS = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name
          + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def make_bgra(w, h, rect):
    """Black frame with a white rectangle at rect=(x, y, rw, rh)."""
    buf = bytearray(w * h * 4)
    x0, y0, rw, rh = rect
    for y in range(y0, y0 + rh):
        base = y * w * 4
        for x in range(x0, x0 + rw):
            o = base + x * 4
            buf[o:o + 4] = b"\xff\xff\xff\xff"
    return bytes(buf)


def centroid_of_white(bgra, w, h):
    xs = ys = n = 0
    for y in range(h):
        base = y * w * 4
        for x in range(w):
            if bgra[base + x * 4] > 200:
                xs += x; ys += y; n += 1
    return (xs / n, ys / n) if n else (None, None)


def main():
    # -- oversized frame: 3840x1080 (4K lower half / dpr-scaled 2560) --
    W, H, MAX = 3840, 1080, 2600
    rect = (1700, 400, 400, 120)   # "subtitle" block
    src = make_bgra(W, H, rect)
    out, nw, nh = downscale_bgra(src, W, H, MAX)
    check("fits under the limit", max(nw, nh) <= MAX, f"{nw}x{nh}")
    check("aspect preserved (±1%)",
          abs(nw / nh - W / H) / (W / H) < 0.01, f"{nw}x{nh}")
    check("buffer length matches new dims", len(out) == nw * nh * 4)

    # feature must land where the recognize() scale-back expects it:
    # boxes in the small image are mapped by (ow/nw, oh/nh)
    cx, cy = centroid_of_white(out, nw, nh)
    check("white feature survives downscale", cx is not None)
    if cx is not None:
        sx, sy = W / nw, H / nh
        want_cx, want_cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        check("scale-back lands on the original position (±2 px)",
              abs(cx * sx - want_cx) <= 2 and abs(cy * sy - want_cy) <= 2,
              f"got ({cx*sx:.1f},{cy*sy:.1f}) want ({want_cx},{want_cy})")

    # -- frame already under the limit: must be byte-identical --------
    W2, H2 = 1280, 220
    src2 = make_bgra(W2, H2, (500, 80, 200, 60))
    out2, nw2, nh2 = downscale_bgra(src2, W2, H2, MAX)
    check("small frame passes through untouched",
          out2 == src2 and (nw2, nh2) == (W2, H2))

    print("PASS" if not FAILS else f"FAIL ({len(FAILS)}: {FAILS})")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
