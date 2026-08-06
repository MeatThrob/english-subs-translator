"""Golden-output regression for the Darija path — the user's daily driver.

The 46 expected translations in golden_darija.json were produced by the
pre-multilingual baseline (commit 5f8e6a9) and verified char-for-char
identical on the multilingual tree; Terjman + CTranslate2 int8 at beam 2
is deterministic, so ANY diff here means a change to the shared
translation path (tokenization, _cleanup, decoding params) altered
Darija behaviour and must be deliberate. Regenerate the golden file only
when such a change is intended:

    .venv/bin/python tests/regress_darija.py --update

Latency is reported for information but not asserted — it depends on
machine load (check `uptime` before reading anything into it).

Run: .venv/bin/python tests/regress_darija.py
"""

import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLDEN = os.path.join(ROOT, "tests", "golden_darija.json")


def main():
    from darija_subs.translate import Translator

    golden: dict[str, str] = json.load(open(GOLDEN, encoding="utf-8"))
    tr = Translator(os.path.join(ROOT, "models", "terjman-nano-int8"),
                    beam_size=2)  # app-default beam; no disk cache
    tr.warmup()

    update = "--update" in sys.argv
    diffs, times, fresh = [], [], {}
    for line, want in golden.items():
        t0 = time.perf_counter()
        got = tr.translate(line, use_cache=False)
        times.append((time.perf_counter() - t0) * 1000)
        fresh[line] = got
        if got != want:
            diffs.append((line, want, got))

    print(f"{len(golden)} lines  median {statistics.median(times):.1f} ms  "
          f"max {max(times):.1f} ms")
    if update:
        json.dump(fresh, open(GOLDEN, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"golden updated ({len(diffs)} lines changed)")
        return 0
    for line, want, got in diffs:
        print(f"DIFF: {line!r}\n  golden : {want!r}\n  current: {got!r}")
    print("PASS" if not diffs else f"FAIL ({len(diffs)}/{len(golden)} differ)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
