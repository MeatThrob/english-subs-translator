"""Per-language translation latency + quality-sample benchmark.

Why: the app routes each target language to a local model
(Terjman-Nano for Darija, NLLB-600M for the rest) and that routing must
be justified by measurement, not vibes. This prints warm per-line
latency stats and a sample translation for a set of representative
subtitle lines, exercising retarget() across languages on one loaded
NLLB instance (which is exactly what the GUI does).

Run: .venv/bin/python tests/bench_translate.py [--beam N] [--threads N]
     [--langs spa_Latn,jpn_Jpan,...]
"""

import argparse
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from darija_subs import langs
from darija_subs.translate import Translator

LINES = [
    "Where have you been all this time?",
    "I don't know what you're talking about.",
    "We need to get out of here, now!",
    "He's not coming back, is he?",
    "Thank you for everything you've done.",
    "What do you want me to say?",
    "It's been a long time since I saw her.",
    "Don't touch that! It's dangerous.",
    "I promise I'll be back before dark.",
    "There's something I have to tell you.",
]

DEFAULT_LANGS = ["ary_Arab", "arb_Arab", "spa_Latn", "jpn_Jpan", "rus_Cyrl"]


def bench_translator(tr: Translator, lines: list[str]) -> tuple[list[float], list[str]]:
    times, outs = [], []
    for s in lines:
        t0 = time.perf_counter()
        out = tr.translate(s, use_cache=False)
        times.append((time.perf_counter() - t0) * 1000)
        outs.append(out)
    return times, outs


def row(name: str, times: list[float]) -> str:
    return (f"{name:34s} med {statistics.median(times):7.1f} ms   "
            f"mean {statistics.mean(times):7.1f} ms   "
            f"min {min(times):6.1f}   max {max(times):7.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beam", type=int, default=2,
                    help="beam size (app default is 2)")
    ap.add_argument("--threads", type=int, default=2,
                    help="CTranslate2 intra_threads")
    ap.add_argument("--langs", type=str, default=",".join(DEFAULT_LANGS))
    ap.add_argument("--show-output", action="store_true",
                    help="print every translated line, not just one sample")
    args = ap.parse_args()
    targets = [c for c in args.langs.split(",") if c]
    for c in targets:
        if c not in langs.LANGS:
            print(f"unknown language: {c}", file=sys.stderr)
            return 2

    models = os.path.join(ROOT, "models")
    print(f"beam={args.beam} intra_threads={args.threads} "
          f"lines={len(LINES)} (warm, cache off)\n")

    results = []

    # Specialist: Terjman for Darija (plus NLLB-Darija for comparison).
    if "ary_Arab" in targets:
        t = Translator(langs.pick_model("ary_Arab", models),
                       target_lang="ary_Arab", beam_size=args.beam,
                       intra_threads=args.threads)
        t.warmup()
        times, outs = bench_translator(t, LINES)
        results.append(("Darija · terjman-nano (77M)", times, outs))
        del t

    # Generalist: one NLLB instance retargeted across all languages —
    # the same object, reloaded caches, no model reload.
    nllb_targets = [c for c in targets] # incl. ary for A/B if requested
    nllb = Translator(os.path.join(models, langs.GENERAL_MODEL),
                      target_lang=nllb_targets[0], beam_size=args.beam,
                      intra_threads=args.threads)
    nllb.warmup()
    for code in nllb_targets:
        nllb.retarget(code)
        times, outs = bench_translator(nllb, LINES)
        results.append((f"{langs.english_name(code)} · nllb-600m", times, outs))

    print("== warm per-line latency ==")
    for name, times, _ in results:
        print(row(name, times))

    print("\n== samples ==")
    for name, _, outs in results:
        if args.show_output:
            print(f"-- {name}")
            for src, out in zip(LINES, outs):
                print(f"   {src!r} -> {out}")
        else:
            print(f"{name:34s} {LINES[0]!r} -> {outs[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
