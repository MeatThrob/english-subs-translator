"""English OCR via Apple's Vision framework (VNRecognizeTextRequest).

Runs fully on-device, hardware-accelerated on Apple Silicon. "accurate"
level handles stylized/outlined subtitle fonts; "fast" is ~3x quicker on
plain text. Both are benchmarked in tests/bench_ocr.py.
"""

import Vision


def ocr_cgimage(cgimage, fast: bool = False, min_confidence: float = 0.3):
    """OCR a CGImage. Returns list of (text, confidence, bbox) sorted
    top-to-bottom. bbox is Vision-normalized (origin bottom-left)."""
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cgimage, None
    )
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelFast
        if fast
        else Vision.VNRequestTextRecognitionLevelAccurate
    )
    req.setRecognitionLanguages_(["en-US"])
    req.setUsesLanguageCorrection_(True)

    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        return []

    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand or not len(cand):
            continue
        top = cand[0]
        if top.confidence() < min_confidence:
            continue
        box = obs.boundingBox()
        out.append((str(top.string()), float(top.confidence()), box))

    # Vision bbox origin is bottom-left; higher y = higher on screen.
    out.sort(key=lambda t: -(t[2].origin.y + t[2].size.height))
    return out


def lines_to_text(lines) -> str:
    """Join OCR lines into one subtitle string."""
    return " ".join(t for t, _c, _b in lines).strip()
