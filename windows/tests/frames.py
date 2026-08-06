"""Synthetic anime-style subtitle frames (white text, black outline,
busy background) as raw BGRA buffers — shared by the OCR benchmarks."""

import random

from PIL import Image, ImageDraw, ImageFont

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

_FONTS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(size=40):
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_frame(text: str, seed: int):
    """Return (bgra_bytes, w, h)."""
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H))
    px = img.load()
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
    font = _font(40)
    bbox = draw.textbbox((0, 0), text, font=font)
    tx = (W - (bbox[2] - bbox[0])) / 2
    ty = (H - (bbox[3] - bbox[1])) / 2
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255),
              stroke_width=3, stroke_fill=(0, 0, 0))

    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    return bgra.tobytes(), W, H
