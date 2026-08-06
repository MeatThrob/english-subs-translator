"""Local English -> any-NLLB-language translation.

Engine: CTranslate2 int8 on-CPU (Apple Silicon friendly, no network at
runtime). Two model families are supported and detected automatically:

  - Marian single-direction models (Terjman-Nano-v2.1: 77M params,
    Darija-specialized, ~40-130 ms/line on an M2) — no target-language
    token exists, so no decoder prefix is used.
  - NLLB-family models (facebook/nllb-200-*, Terjman-Ultra) — the
    target language is a real token and is passed as a decoder prefix,
    which is how one model serves 202 languages.

The disk cache is sharded per (model, target language): each pair gets
its own JSON file, so switching from Spanish to Japanese can never
replay a Spanish line from cache. `retarget()` switches an NLLB
instance to a new language without reloading the 600 MB model.
"""

import json
import os
import re
import threading
import time

import ctranslate2

from . import langs

_DIACRITICS = re.compile("[ً-ْٰ]")


def _quiet_tokenizer_noise():
    """Silence two known-noisy, non-actionable tokenizer-load warnings.

    Suppression only — behaviour must not change:
      - transformers' "incorrect regex pattern / fix_mistral_regex" note
        targets Mistral tokenizers, not NLLB; setting the flag could
        alter tokenization, so we hide the message instead.
      - Marian's "pip install sacremoses" hint: installing it would
        change Marian detokenization, which the Darija golden test pins.
    """
    import logging
    import warnings

    warnings.filterwarnings("ignore", message=r".*sacremoses.*")

    class _Drop(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "incorrect regex pattern" not in record.getMessage()

    drop = _Drop()
    root = logging.getLogger("transformers")
    root.addFilter(drop)
    # A logger's filters run only for records emitted through THAT logger;
    # a parent's filters are skipped for its children's records (only the
    # parent's handlers are reached). The note comes from the child logger
    # transformers.tokenization_utils_base, so filtering the parent alone
    # does nothing — the filter has to sit on the handler every child's
    # record passes through.
    from transformers.utils import logging as _hf_logging
    _hf_logging.get_logger()          # force the library handler to exist
    for handler in root.handlers:
        handler.addFilter(drop)


def cache_path_for(cache_dir: str, model_dir: str, target_lang: str) -> str:
    """One cache file per (model, target) — the shard IS the cache key."""
    return os.path.join(
        cache_dir, f"{os.path.basename(os.path.normpath(model_dir))}__{target_lang}.json"
    )


def _cleanup(source_en: str, out: str, target_lang: str) -> str:
    # Arabic-script models often emit fully-vowelled (diacritized) text,
    # which subtitles never use; the regex is a no-op guard elsewhere,
    # but only Arabic-script targets should pay for it at all.
    if langs.script(target_lang) == "Arab":
        out = _DIACRITICS.sub("", out)
    out = out.strip()
    # Some models wrap the whole line in quotes the source never had.
    # Only strip a full symmetric wrap when the source contains no quote
    # of any kind — French/Russian «...» quoting a quoted source stays.
    pairs = {'"': '"', "«": "»", "„": "“", "‟": "”"}
    if (out[:1] in pairs and out[-1:] == pairs[out[:1]]
            and not any(q in source_en for q in '"«»')):
        out = out[1:-1].strip()
    return out


class Translator:
    def __init__(
        self,
        model_dir: str,
        target_lang: str = langs.DEFAULT_TARGET,
        source_lang: str = "eng_Latn",
        beam_size: int = 1,
        intra_threads: int = 2,
        cache_dir: str | None = None,
    ):
        _quiet_tokenizer_noise()
        from transformers import AutoTokenizer  # deferred: slow import

        self.model_dir = model_dir
        self.beam_size = beam_size
        self.translator = ctranslate2.Translator(
            model_dir,
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=intra_threads,
        )
        # NLLB-family tokenizers need src_lang; Marian ones reject it.
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_dir, src_lang=source_lang
            )
        except Exception:
            self.tokenizer = AutoTokenizer.from_pretrained(model_dir)

        self.cache_dir = cache_dir
        self.cache_path: str | None = None
        self.cache: dict[str, str] = {}
        self._dirty = 0
        self._lock = threading.Lock()
        self.target_lang = target_lang
        self._apply_target(target_lang)

    # ---- target language ------------------------------------------
    def _apply_target(self, target_lang: str):
        """Set decoder prefix + cache shard for `target_lang`.

        NLLB-style models need the target language as a decoder prefix;
        single-direction Marian models must not get one (their tokenizer
        has no such token, so the id resolves to <unk>).
        """
        self.target_lang = target_lang
        tl_id = self.tokenizer.convert_tokens_to_ids(target_lang)
        unk = getattr(self.tokenizer, "unk_token_id", None)
        self.target_prefix = (
            [target_lang] if (tl_id is not None and tl_id != unk) else None
        )
        if self.target_prefix is None and target_lang not in langs.SPECIALIST_MODELS:
            raise ValueError(
                f"model {self.model_dir} has no token for {target_lang!r} "
                f"and is not a known specialist for it"
            )
        self.cache = {}
        self._dirty = 0
        self.cache_path = None
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            self.cache_path = cache_path_for(
                self.cache_dir, self.model_dir, target_lang
            )
            if os.path.exists(self.cache_path):
                try:
                    with open(self.cache_path, "r", encoding="utf-8") as f:
                        self.cache = json.load(f)
                except Exception:
                    self.cache = {}

    def retarget(self, target_lang: str):
        """Switch output language in place (NLLB models only) — saves the
        old cache shard, loads the new one, keeps the loaded weights."""
        if target_lang == self.target_lang:
            return
        with self._lock:
            self._save_locked()
        self._apply_target(target_lang)

    # ---- translation ----------------------------------------------
    def warmup(self):
        self.translate("Hello, how are you?", use_cache=False)

    def translate(self, text: str, use_cache: bool = True) -> str:
        if use_cache:
            with self._lock:
                hit = self.cache.get(text)
            if hit is not None:
                return hit

        tokens = self.tokenizer.convert_ids_to_tokens(
            self.tokenizer.encode(text)
        )
        kw = dict(
            beam_size=self.beam_size,
            max_decoding_length=128,
            no_repeat_ngram_size=4,
        )
        if self.target_prefix:
            kw["target_prefix"] = [self.target_prefix]
        results = self.translator.translate_batch([tokens], **kw)
        hyp = results[0].hypotheses[0]
        if self.target_prefix and hyp and hyp[0] == self.target_prefix[0]:
            hyp = hyp[1:]
        out = self.tokenizer.decode(
            self.tokenizer.convert_tokens_to_ids(hyp),
            skip_special_tokens=True,
        )
        out = _cleanup(text, out, self.target_lang)

        if use_cache and out:
            with self._lock:
                self.cache[text] = out
                self._dirty += 1
                if self.cache_path and self._dirty >= 20:
                    self._save_locked()
        return out

    # ---- cache persistence ----------------------------------------
    def _save_locked(self):
        if not self.cache_path or not self._dirty:
            return
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
            self._dirty = 0
        except Exception:
            pass

    def save_cache(self):
        with self._lock:
            self._save_locked()


def migrate_legacy_cache(legacy_path: str, cache_dir: str, model_dir: str):
    """One-time move of the old single-file cache.json into the sharded
    layout. The old cache was only ever written by the Darija default
    (Terjman, ary_Arab), so that is the shard it belongs to; leaving it
    where it was would silently serve Darija lines to other languages."""
    if not os.path.exists(legacy_path):
        return
    dest = cache_path_for(cache_dir, model_dir, "ary_Arab")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(dest):
            with open(legacy_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            with open(dest, "r", encoding="utf-8") as f:
                new = json.load(f)
            old.update(new)  # newer entries win
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(old, f, ensure_ascii=False)
            os.remove(legacy_path)
        else:
            os.replace(legacy_path, dest)
    except Exception:
        pass  # worst case: legacy file stays, unused


def bench(translator: Translator, samples: list[str]) -> list[tuple[str, str, float]]:
    out = []
    for s in samples:
        t0 = time.perf_counter()
        tr = translator.translate(s, use_cache=False)
        out.append((s, tr, (time.perf_counter() - t0) * 1000))
    return out
