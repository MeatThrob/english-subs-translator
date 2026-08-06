"""Headless unit checks for the multi-language plumbing.

Covers the failure modes that motivated the redesign:
  - cache sharding: a Spanish line can never be served to Japanese
  - retarget(): same NLLB instance, different language, correct output
  - legacy cache.json migration lands in the Darija/Terjman shard
  - _cleanup: diacritics stripped only for Arabic script; wrapping
    quotes stripped only when the source had none

Run: .venv/bin/python tests/test_multilang_unit.py
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from darija_subs import langs
from darija_subs.translate import (Translator, _cleanup, cache_path_for,
                                   migrate_legacy_cache)

FAILS = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    models = os.path.join(ROOT, "models")
    tmp = tempfile.mkdtemp(prefix="darija_subs_test_")
    try:
        # --- _cleanup behaviour -----------------------------------
        check("diacritics stripped for Arabic target",
              _cleanup("hi", "مَرْحَبًا", "ary_Arab") == "مرحبا")
        check("diacritics untouched for Latin target",
              _cleanup("hi", "hóla", "spa_Latn") == "hóla")
        check("spurious wrap quotes stripped (source unquoted)",
              _cleanup("hello", "«hola»", "spa_Latn") == "hola")
        check("legit guillemets kept (source quoted)",
              _cleanup('he said "run"', "«cours» dit-il", "fra_Latn")
              == "«cours» dit-il")

        # --- legacy migration -------------------------------------
        legacy = os.path.join(tmp, "cache.json")
        cache_dir = os.path.join(tmp, "cache")
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump({"Hello.": "سلام."}, f, ensure_ascii=False)
        tn = os.path.join(models, "terjman-nano-int8")
        migrate_legacy_cache(legacy, cache_dir, tn)
        shard = cache_path_for(cache_dir, tn, "ary_Arab")
        migrated = json.load(open(shard, encoding="utf-8"))
        check("legacy cache migrated to Darija shard",
              migrated.get("Hello.") == "سلام." and not os.path.exists(legacy))

        # --- cache sharding + retarget on one NLLB instance -------
        nllb = os.path.join(models, "nllb-600m-int8")
        tr = Translator(nllb, target_lang="spa_Latn", beam_size=1,
                        cache_dir=cache_dir)
        es = tr.translate("Thank you very much.")
        check("Spanish output is Latin",
              bool(es) and all(ord(c) < 0x370 for c in es), es)
        tr.retarget("jpn_Jpan")
        ja = tr.translate("Thank you very much.")
        check("retarget to Japanese changes output script",
              bool(ja) and any(ord(c) > 0x3000 for c in ja), ja)
        check("Japanese cache does not contain the Spanish line",
              tr.cache.get("Thank you very much.") == ja != es)
        tr.save_cache()
        es_shard = cache_path_for(cache_dir, nllb, "spa_Latn")
        ja_shard = cache_path_for(cache_dir, nllb, "jpn_Jpan")
        check("separate shard files exist",
              os.path.exists(es_shard) and os.path.exists(ja_shard))
        check("shards hold their own language",
              json.load(open(es_shard))["Thank you very much."] == es
              and json.load(open(ja_shard))["Thank you very much."] == ja)

        # retarget back must re-serve the Spanish line from cache
        tr.retarget("spa_Latn")
        check("retarget back reloads the Spanish shard",
              tr.translate("Thank you very much.") == es)

        # --- routing guard ----------------------------------------
        try:
            Translator(os.path.join(models, "terjman-nano-int8"),
                       target_lang="spa_Latn")
            check("terjman refuses non-Darija target", False)
        except ValueError:
            check("terjman refuses non-Darija target", True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("PASS" if not FAILS else f"FAIL ({len(FAILS)}: {FAILS})")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
