"""Decide which OCR'd lines in the scan band are actual subtitles.

Subtitles: white lettering (bare or on a dark box), decently sized,
roughly horizontally centered. Page links, player UI, timestamps and
thumbnails fail one of those tests.
"""

import Quartz

# near-white threshold for subtitle fill (subs are white per spec;
# antialiasing keeps genuine strokes well above 200)
WHITE = 200
MIN_WHITE_RATIO = 0.02   # fraction of sampled bbox pixels that are white
MIN_H_POINTS = 21        # min text height (pt): desktop/chat text is 15-18, real subs 29+
CENTER_BAND = (0.08, 0.92)  # line center-x must fall in this region fraction


def cgimage_pixels(img):
    """Raw BGRA bytes + geometry for pixel sampling."""
    prov = Quartz.CGImageGetDataProvider(img)
    data = Quartz.CGDataProviderCopyData(prov)
    return (
        bytes(data),
        Quartz.CGImageGetBytesPerRow(img),
        Quartz.CGImageGetWidth(img),
        Quartz.CGImageGetHeight(img),
    )


def is_subtitle_line(buf, bpr, iw, ih, box, region_h_points,
                     min_h_points=MIN_H_POINTS,
                     min_white_ratio=MIN_WHITE_RATIO):
    """box is a Vision normalized bbox (origin bottom-left)."""
    cx = box.origin.x + box.size.width / 2
    if not (CENTER_BAND[0] < cx < CENTER_BAND[1]):
        return False

    scale = ih / max(1.0, region_h_points)  # retina-safe
    h_px = box.size.height * ih
    if h_px < min_h_points * scale:
        return False

    x0 = max(0, int(box.origin.x * iw))
    w = min(iw - x0, max(1, int(box.size.width * iw)))
    y0 = max(0, int((1 - box.origin.y - box.size.height) * ih))
    h = min(ih - y0, max(1, int(h_px)))

    row_step = max(1, h // 16)
    col_step = max(1, w // 160)
    whites = samples = 0
    for y in range(y0, y0 + h, row_step):
        base = y * bpr
        for x in range(x0, x0 + w, col_step):
            o = base + x * 4
            # BGRA
            if buf[o] > WHITE and buf[o + 1] > WHITE and buf[o + 2] > WHITE:
                whites += 1
            samples += 1
    return samples > 0 and (whites / samples) >= min_white_ratio


def _cluster_score(cluster):
    """Prefer the cluster that looks most like a subtitle stack.

    Text volume matters as much as size: a short but tall scrap of UI
    ("O ES") must never outrank a real sentence rendered smaller.
    """
    hs = sorted(l[2].size.height for l in cluster)
    h_med = hs[len(hs) // 2]
    chars = sum(len(l[0]) for l in cluster)
    words = sum(len(l[0].split()) for l in cluster)
    if chars < 8 or words < 2:      # not a sentence — heavily penalised
        chars *= 0.15
    cxs = [l[2].origin.x + l[2].size.width / 2 for l in cluster]
    cx = sum(cxs) / len(cxs)
    top = max(l[2].origin.y + l[2].size.height for l in cluster)
    return (chars * h_med
            * (1.0 - 0.6 * abs(cx - 0.5))
            * (0.7 + 0.3 * (1.0 - top)))


def _clusters(accepted):
    """Group lines into vertically-adjacent, center-aligned stacks.
    Input must be sorted top-to-bottom (as ocr_cgimage returns)."""
    clusters = []
    for line in accepted:
        box = line[2]
        placed = False
        for cl in clusters:
            pbox = cl[-1][2]
            gap = pbox.origin.y - (box.origin.y + box.size.height)
            max_h = max(pbox.size.height, box.size.height)
            pcx = pbox.origin.x + pbox.size.width / 2
            ccx = box.origin.x + box.size.width / 2
            if gap <= 1.5 * max_h and abs(pcx - ccx) <= 0.25:
                cl.append(line)
                placed = True
                break
        if not placed:
            clusters.append([line])
    return clusters


def filter_lines(img, lines, region_h_points):
    """Return (subtitle_lines, union_box). Lines are filtered for
    subtitle looks, then clustered spatially and only the best cluster
    kept — this drops banners/ads that pass the color test but sit
    apart from the actual subtitle stack."""
    if not lines:
        return [], None
    buf, bpr, iw, ih = cgimage_pixels(img)
    accepted = [
        (text, conf, box)
        for text, conf, box in lines
        if is_subtitle_line(buf, bpr, iw, ih, box, region_h_points)
    ]
    if not accepted:
        return [], None

    best = max(_clusters(accepted), key=_cluster_score)

    x0 = min(l[2].origin.x for l in best)
    y0 = min(l[2].origin.y for l in best)
    x1 = max(l[2].origin.x + l[2].size.width for l in best)
    y1 = max(l[2].origin.y + l[2].size.height for l in best)
    return best, (x0, y0, x1 - x0, y1 - y0)
