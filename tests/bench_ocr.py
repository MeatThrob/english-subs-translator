"""Benchmark Apple Vision OCR (fast vs accurate) on synthetic
anime-style subtitle frames: white text, black outline, busy background.

Run: .venv/bin/python tests/bench_ocr.py
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw, ImageFont
import Quartz

from darija_subs.platform.mac.ocr import ocr_cgimage, lines_to_text
from darija_subs.textnorm import normalize

LINES = [
    "I'll never give up, no matter what happens!",
    "What are you doing here? This place is dangerous.",
    "He's the strongest swordsman in the whole kingdom.",
    "Don't worry about me. Just run!",
    "One day, I will become the Pirate King!",
    "Sensei, please teach me that technique.",
    "It can't be... he was supposed to be dead!",
    "Thank you for everything, my friend.",
    "Where did everyone go?",
    "If you touch my friends, I'll destroy you.",
]

W, H = 1280, 220


def make_frame(text: str, seed: int) -> str:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H))
    px = img.load()
    # noisy 'video' background: random soft blobs
    base = (rng.randint(10, 120), rng.randint(10, 120), rng.randint(10, 120))
    for y in range(H):
        for x in range(0, W, 4):
            v = (
                base[0] + (x * 7 + y * 13 + seed * 29) % 60,
                base[1] + (x * 3 + y * 11) % 60,
                base[2] + (x * 5 + y * 7) % 60,
            )
            for dx in range(4):
                if x + dx < W:
                    px[x + dx, y] = v

    draw = ImageDraw.Draw(img)
    font = None
    for name in ("/System/Library/Fonts/Helvetica.ttc",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except Exception:
            pass
    bbox = draw.textbbox((0, 0), text, font=font)
    tx = (W - (bbox[2] - bbox[0])) / 2
    ty = (H - (bbox[3] - bbox[1])) / 2
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255),
              stroke_width=3, stroke_fill=(0, 0, 0))
    path = f"/tmp/darija_ocr_test_{seed}.png"
    img.save(path)
    return path


def load_cgimage(path: str):
    url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, path.encode(), len(path.encode()), False
    )
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def main():
    frames = [(t, load_cgimage(make_frame(t, i))) for i, t in enumerate(LINES)]
    for fast in (False, True):
        label = "fast" if fast else "accurate"
        correct = 0
        times = []
        # warmup
        ocr_cgimage(frames[0][1], fast=fast)
        for truth, cg in frames:
            t0 = time.perf_counter()
            lines = ocr_cgimage(cg, fast=fast)
            times.append((time.perf_counter() - t0) * 1000)
            got = normalize(lines_to_text(lines))
            want = normalize(truth)
            ok = got == want
            correct += ok
            if not ok:
                print(f"  [{label}] MISMATCH: want={want!r} got={got!r}")
        avg = sum(times) / len(times)
        print(
            f"{label:8s}: {correct}/{len(frames)} exact, "
            f"avg {avg:.0f} ms, min {min(times):.0f}, max {max(times):.0f}"
        )


if __name__ == "__main__":
    main()
