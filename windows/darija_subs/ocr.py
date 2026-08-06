"""Pluggable OCR backends over raw BGRA pixel buffers.

Backends:
  winrt  - Windows.Media.Ocr (built into Windows 10/11, on-device, fast)
  vision - Apple Vision (used when validating this codebase on macOS)
  rapid  - RapidOCR/ONNX (optional pip extra; fallback if winrt quality
           disappoints on a particular subtitle style)

All return list[(text, confidence, (x, y, w, h))] ordered top-to-bottom,
with bounding boxes in image pixels (top-left origin).
"""

import sys


def get_backend(name: str = "auto"):
    if name == "auto":
        name = "winrt" if sys.platform == "win32" else "vision"
    if name == "winrt":
        return WinRTBackend()
    if name == "vision":
        return VisionBackend()
    if name == "rapid":
        return RapidBackend()
    raise ValueError(f"unknown OCR backend: {name}")


def lines_to_text(lines) -> str:
    return " ".join(t for t, _c, _b in lines).strip()


def downscale_bgra(bgra: bytes, w: int, h: int, max_dim: int):
    """Shrink a BGRA buffer so max(w, h) <= max_dim.

    Returns (bgra, new_w, new_h). WinRT's OcrEngine rejects images
    larger than OcrEngine.MaxImageDimension (~2600 px) outright — and
    the default no-pick region is the full lower half of the screen, so
    on any scaled display (dpr 1.25+) or 4K the physical-pixel width
    exceeds the limit and OCR would fail on every frame. Subtitles are
    large; they survive this downscale easily.
    """
    if max(w, h) <= max_dim:
        return bgra, w, h
    from PIL import Image
    scale = max_dim / float(max(w, h))
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = Image.frombytes("RGBA", (w, h), bgra, "raw", "BGRA")
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.tobytes("raw", "BGRA"), nw, nh


class WinRTBackend:
    """Windows.Media.Ocr via the winsdk (or modular winrt) bindings."""

    def __init__(self, lang: str = "en-US"):
        import asyncio
        try:
            from winsdk.windows.media.ocr import OcrEngine
            from winsdk.windows.globalization import Language
            from winsdk.windows.graphics.imaging import (
                SoftwareBitmap, BitmapPixelFormat,
            )
            from winsdk.windows.security.cryptography import (
                CryptographicBuffer,
            )
        except ImportError:
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import (
                SoftwareBitmap, BitmapPixelFormat,
            )
            from winrt.windows.security.cryptography import (
                CryptographicBuffer,
            )

        self._SoftwareBitmap = SoftwareBitmap
        self._BGRA8 = BitmapPixelFormat.BGRA8
        self._CryptoBuf = CryptographicBuffer
        self.engine = OcrEngine.try_create_from_language(Language(lang))
        if self.engine is None:
            self.engine = OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError(
                "No OCR language available. Install 'English (United States)' "
                "in Settings > Time & Language > Language & region."
            )
        # WinRT OCR hard-rejects images above this dimension. On scaled
        # displays the default full-width lower-half region exceeds it in
        # physical pixels, so recognize() downscales to fit.
        try:
            self.max_dim = int(OcrEngine.max_image_dimension)
        except Exception:
            self.max_dim = 2600
        self._loop = asyncio.new_event_loop()

    def recognize(self, bgra: bytes, w: int, h: int):
        ow, oh = w, h
        bgra, w, h = downscale_bgra(bgra, w, h, self.max_dim)
        sx, sy = ow / w, oh / h   # map result boxes back to input pixels
        ibuf = self._CryptoBuf.create_from_byte_array(bgra)
        bmp = self._SoftwareBitmap.create_copy_from_buffer(
            ibuf, self._BGRA8, w, h
        )
        result = self._loop.run_until_complete(
            self.engine.recognize_async(bmp)
        )
        out = []
        for line in result.lines:
            rects = [wd.bounding_rect for wd in line.words]
            if not rects:
                continue
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
            out.append(
                (str(line.text), 1.0,
                 (x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy))
            )
        out.sort(key=lambda t: t[2][1])
        return out


class VisionBackend:
    """Apple Vision on a BGRA buffer (mac-side validation of this port)."""

    def __init__(self, min_confidence: float = 0.3):
        import Quartz
        import Vision
        self.Quartz = Quartz
        self.Vision = Vision
        self.min_confidence = min_confidence
        self._cs = Quartz.CGColorSpaceCreateDeviceRGB()

    def recognize(self, bgra: bytes, w: int, h: int):
        Q, V = self.Quartz, self.Vision
        provider = Q.CGDataProviderCreateWithData(None, bgra, len(bgra), None)
        cg = Q.CGImageCreate(
            w, h, 8, 32, w * 4, self._cs,
            Q.kCGImageAlphaNoneSkipFirst | Q.kCGBitmapByteOrder32Little,
            provider, None, False, Q.kCGRenderingIntentDefault,
        )
        handler = V.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg, None
        )
        req = V.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(V.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(["en-US"])
        req.setUsesLanguageCorrection_(True)
        ok, _err = handler.performRequests_error_([req], None)
        if not ok:
            return []
        out = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)
            if not cand or not len(cand):
                continue
            top = cand[0]
            if top.confidence() < self.min_confidence:
                continue
            b = obs.boundingBox()  # normalized, origin bottom-left
            px = (
                b.origin.x * w,
                (1 - b.origin.y - b.size.height) * h,
                b.size.width * w,
                b.size.height * h,
            )
            out.append((str(top.string()), float(top.confidence()), px))
        out.sort(key=lambda t: t[2][1])
        return out


class RapidBackend:
    """RapidOCR (PaddleOCR models on ONNX Runtime). Optional:
    pip install rapidocr-onnxruntime"""

    def __init__(self):
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        self.np = np
        self.engine = RapidOCR()

    def recognize(self, bgra: bytes, w: int, h: int):
        np = self.np
        arr = np.frombuffer(bgra, dtype=np.uint8).reshape(h, w, 4)
        bgr = arr[:, :, :3].copy()
        result, _elapse = self.engine(bgr)
        if not result:
            return []
        items = []
        for box, text, score in result:
            if score < 0.4:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append(
                (str(text), float(score),
                 (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
            )
        items.sort(key=lambda t: t[2][1])
        return items
