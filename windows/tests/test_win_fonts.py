"""Script-aware rendering checks for the Qt overlay's font cascade.

For every script in the 202-language registry this verifies, on the
machine it runs on:

  1. font_for() returns the intended per-script family cascade (pure
     table check — proves CJK/Indic/Thai/... don't silently fall into
     the default, and that no macOS-only font leaks in);
  2. direction: RTL exactly for Arabic/Hebrew scripts;
  3. no tofu: a sample of the language's native name is rendered into
     an offscreen image and compared against the same string length of
     guaranteed-missing glyphs (U+10FFFD). If the two renders are
     pixel-identical, every character fell back to the .notdef box —
     the silent-tofu failure this test exists to catch.

On macOS (validation) check 3 exercises the mechanism against the Mac's
font fallback; the Windows families named in the cascade only resolve on
real Windows, so run this test there too — that run is the real proof.

Run: .venv\\Scripts\\python windows\\tests\\test_win_fonts.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from darija_subs import langs
from darija_subs.platform.win.overlay import _SCRIPT_FONTS, font_for

FAILS = []

# scripts that need a non-default (non-"Segoe UI"-only) family on Windows
NEEDS_SPECIFIC = {"Jpan", "Hang", "Hans", "Hant", "Deva", "Beng", "Guru",
                  "Gujr", "Orya", "Taml", "Telu", "Knda", "Mlym", "Sinh",
                  "Thai", "Laoo", "Khmr", "Mymr", "Ethi", "Tfng", "Tibt"}

MAC_ONLY_FONTS = {"SF Arabic", "Geeza Pro", "SF Pro", "Helvetica Neue"}


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name
          + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def render(font, text):
    img = QImage(600, 90, QImage.Format.Format_ARGB32)
    img.fill(QColor("black"))
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 0, 600, 90), Qt.AlignLeft | Qt.AlignVCenter, text)
    p.end()
    return img


def main():
    QGuiApplication([])

    # one representative language per script, native-name sample text
    per_script = {}
    for code, (_en, native) in langs.LANGS.items():
        per_script.setdefault(langs.script(code), (code, native))

    for script, (code, native) in sorted(per_script.items()):
        f = font_for(code, 40)
        fams = f.families()

        if script in NEEDS_SPECIFIC:
            check(f"{script}: cascade names a script font",
                  fams[0] == _SCRIPT_FONTS[script][0], f"{fams[:2]}")
        check(f"{script}: Segoe UI fallback present", "Segoe UI" in fams)
        check(f"{script}: no macOS-only font in cascade",
              not (set(fams) & MAC_ONLY_FONTS), f"{fams}")
        check(f"{script}: direction",
              langs.is_rtl(code) == (script in ("Arab", "Hebr")))

        sample = native if any(not c.isascii() for c in native) else native
        tofu = "\U0010FFFD" * max(1, len(sample.strip()))
        a = render(f, sample)
        b = render(f, tofu)
        check(f"{script}: glyphs resolve (no tofu) for {code}",
              a != b, f"sample {sample!r} rendered identical to .notdef")

    print("PASS" if not FAILS else f"FAIL ({len(FAILS)}: {FAILS[:6]}...)")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
