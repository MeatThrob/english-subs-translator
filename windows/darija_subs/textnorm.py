"""Normalization of OCR'd subtitle text.

Stabilizes OCR jitter so the cache hits and the overlay doesn't
re-render on cosmetic differences.
"""

import re

_MUSIC = "♪♫♩♬"  # ♪ ♫ ♩ ♬

_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
    "|": "I",  # classic OCR confusion in all-caps subs
}


def normalize(text: str) -> str:
    for a, b in _REPLACEMENTS.items():
        text = text.replace(a, b)
    # strip music-note decorations but keep the lyric text
    text = text.strip().strip(_MUSIC).strip()
    # collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # never feed our own Arabic overlay back in as source text — the
    # capture-exclusion (WDA_EXCLUDEFROMCAPTURE) should make this
    # unreachable, but that call is per-machine (test_exclusion.py), so
    # keep the software guard too
    if re.search(r"[؀-ۿ]", text):
        return ""
    # drop pure-junk reads: need a real word (3+ consecutive letters)
    if not re.search(r"[A-Za-z]{3}", text):
        return ""
    return text


def is_probably_partial(prev: str, new: str) -> bool:
    """True if `new` looks like a fade-in prefix of a longer line —
    the caller may still show it and refine on the next tick."""
    return bool(prev) and new != prev and prev.startswith(new)
