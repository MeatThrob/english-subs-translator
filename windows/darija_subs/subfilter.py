"""Decide which OCR'd lines in the scan band are actual subtitles.

Pixel-coordinate version (top-left origin) for the mss/Qt port.
Subtitles: white lettering (bare or on a dark box), decently sized,
roughly horizontally centered, stacked tightly. Page links, banners,
player UI and thumbnails fail one of those tests; spatially separate
elements are dropped by clustering.
"""

WHITE = 200
MIN_WHITE_RATIO = 0.02
MIN_H_POINTS = 21        # min text height in logical points
CENTER_BAND = (0.08, 0.92)


def is_subtitle_line(bgra, iw, ih, box, scale):
    """box = (x, y, w, h) in image pixels; scale = pixels per point."""
    x, y, w, h = box
    cx = (x + w / 2) / max(1, iw)
    if not (CENTER_BAND[0] < cx < CENTER_BAND[1]):
        return False
    if h < MIN_H_POINTS * scale:
        return False

    x0 = max(0, int(x))
    y0 = max(0, int(y))
    w = min(iw - x0, max(1, int(w)))
    h = min(ih - y0, max(1, int(h)))
    bpr = iw * 4

    row_step = max(1, h // 16)
    col_step = max(1, w // 160)
    whites = samples = 0
    for yy in range(y0, y0 + h, row_step):
        base = yy * bpr
        for xx in range(x0, x0 + w, col_step):
            o = base + xx * 4
            if bgra[o] > WHITE and bgra[o + 1] > WHITE and bgra[o + 2] > WHITE:
                whites += 1
            samples += 1
    return samples > 0 and (whites / samples) >= MIN_WHITE_RATIO


def _cluster_score(cluster, iw, ih):
    """Prefer the cluster that looks most like a subtitle stack.

    Text volume matters as much as size: a short but tall scrap of UI
    ("O ES") must never outrank a real sentence rendered smaller.
    (Same scoring as the macOS tree, translated to pixel coords.)
    """
    hs = sorted(l[2][3] for l in cluster)
    h_med = hs[len(hs) // 2]
    chars = sum(len(l[0]) for l in cluster)
    words = sum(len(l[0].split()) for l in cluster)
    if chars < 8 or words < 2:      # not a sentence — heavily penalised
        chars *= 0.15
    cx = sum(l[2][0] + l[2][2] / 2 for l in cluster) / len(cluster) / max(1, iw)
    bottom = max(l[2][1] + l[2][3] for l in cluster) / max(1, ih)
    return (chars * h_med
            * (1.0 - 0.6 * abs(cx - 0.5))
            * (0.7 + 0.3 * bottom))


def _clusters(accepted, iw):
    """Group lines into vertically-adjacent, center-aligned stacks.
    Input sorted top-to-bottom; pixel coords, y grows downward."""
    clusters = []
    for line in accepted:
        x, y, w, h = line[2]
        placed = False
        for cl in clusters:
            px, py, pw, ph = cl[-1][2]
            gap = y - (py + ph)
            max_h = max(ph, h)
            if gap <= 1.5 * max_h and abs((px + pw / 2) - (x + w / 2)) <= 0.25 * iw:
                cl.append(line)
                placed = True
                break
        if not placed:
            clusters.append([line])
    return clusters


def filter_lines(bgra, iw, ih, lines, scale):
    """lines: [(text, conf, (x,y,w,h))] sorted top-to-bottom.
    Returns (subtitle_lines, union_box_px) keeping only the best
    subtitle-looking cluster."""
    if not lines:
        return [], None
    accepted = [
        l for l in lines if is_subtitle_line(bgra, iw, ih, l[2], scale)
    ]
    if not accepted:
        return [], None

    best = max(_clusters(accepted, iw), key=lambda c: _cluster_score(c, iw, ih))

    x0 = min(l[2][0] for l in best)
    y0 = min(l[2][1] for l in best)
    x1 = max(l[2][0] + l[2][2] for l in best)
    y1 = max(l[2][1] + l[2][3] for l in best)
    return best, (x0, y0, x1 - x0, y1 - y0)
