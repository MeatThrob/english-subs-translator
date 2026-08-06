"""OCR backend benchmark on synthetic subtitle frames.

Run on the target machine:
  .venv\\Scripts\\python tests\\bench_ocr.py            (auto backend)
  .venv\\Scripts\\python tests\\bench_ocr.py rapid      (optional backend)

Prints exact-match accuracy and latency. On Windows expect `winrt`;
if accuracy is below ~8/10, pip install rapidocr-onnxruntime and
compare, then run the app with --ocr rapid.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from darija_subs.platform.win.ocr import get_backend, lines_to_text
from darija_subs.textnorm import normalize
from frames import LINES, make_frame


def main():
    names = sys.argv[1:] or ["auto"]
    frames = [(t, make_frame(t, i)) for i, t in enumerate(LINES)]
    for name in names:
        try:
            backend = get_backend(name)
        except Exception as e:
            print(f"{name}: UNAVAILABLE ({e})")
            continue
        label = type(backend).__name__
        backend.recognize(*frames[0][1])  # warmup
        correct = 0
        times = []
        for truth, (buf, w, h) in frames:
            t0 = time.perf_counter()
            lines = backend.recognize(buf, w, h)
            times.append((time.perf_counter() - t0) * 1000)
            got = normalize(lines_to_text(lines))
            want = normalize(truth)
            ok = got == want
            correct += ok
            if not ok:
                print(f"  [{label}] MISMATCH: want={want!r} got={got!r}")
        print(f"{label}: {correct}/{len(frames)} exact, "
              f"avg {sum(times)/len(times):.0f} ms, "
              f"min {min(times):.0f}, max {max(times):.0f}")


if __name__ == "__main__":
    main()
