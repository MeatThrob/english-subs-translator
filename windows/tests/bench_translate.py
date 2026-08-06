"""Quick translation sanity + latency check on the target machine.

Run: .venv\\Scripts\\python tests\\bench_translate.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from darija_subs.translate import Translator, bench

MODEL = os.path.join(os.path.dirname(__file__), "..", "models",
                     "terjman-nano-int8")

SAMPLES = [
    "I'll never give up, no matter what happens!",
    "What are you doing here? This place is dangerous.",
    "Don't worry about me. Just run!",
    "One day, I will become the Pirate King!",
    "Thank you for everything, my friend.",
]


def main():
    tr = Translator(MODEL)
    tr.warmup()
    for en, ar, ms in bench(tr, SAMPLES):
        print(f"{ms:6.0f} ms | {en}")
        print(f"          | {ar}")


if __name__ == "__main__":
    main()
