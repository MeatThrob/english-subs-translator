"""Pure-math pieces shared by the AppKit (macOS) and Qt (Windows)
control panels: hex<->rgb, the HSV colour-wheel geometry and bitmap,
and the config.json snapshot/restore used by the GUI self-tests.

Nothing in here may import a UI toolkit.
"""

import colorsys
import math
import os

from .pipeline import CONFIG_PATH


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


def wheel_rgb_at(size, x, y, y_down=False):
    """Pure geometry: which colour lives at (x, y) on a `size` disc.
    Points outside the disc clamp to the rim. y_down=True for toolkits
    whose y axis grows downward (Qt)."""
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


def wheel_rgba(size):
    """HSV disc as an (size, size, 4) uint8 RGBA array: hue around,
    saturation outward, full value, 1px feathered edge."""
    import numpy as np

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
    return rgba


def config_snapshot():
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
