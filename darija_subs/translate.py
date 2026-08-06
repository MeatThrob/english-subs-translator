"""Local English -> Moroccan Arabic (Darija) translation.

Engine: CTranslate2 int8 on-CPU (Apple Silicon friendly, no network at
runtime). Default model is Terjman-Nano-v2.1 (Marian arch, 77M params,
Darija-specialized, ~40-130 ms/line on an M2). Any NLLB-arch model
(facebook/nllb-200-*, Terjman-Ultra) is also supported and detected
automatically via the `ary_Arab` language token.

Every translated line is cached (RAM + JSON on disk), so a rewatched
episode or a repeated line renders instantly.
"""

import json
import os
import re
import threading
import time

import ctranslate2

_DIACRITICS = re.compile("[ً-ْٰ]")


def _cleanup(source_en: str, arabic: str, strip_diacritics: bool = True) -> str:
    if strip_diacritics:
        arabic = _DIACRITICS.sub("", arabic)
    arabic = arabic.strip()
    # some models wrap output in quotes the source never had
    if arabic[:1] in "\"«" and arabic[-1:] in "\"»" and source_en[:1] != '"':
        arabic = arabic[1:-1].strip()
    return arabic


class Translator:
    def __init__(
        self,
        model_dir: str,
        target_lang: str = "ary_Arab",
        source_lang: str = "eng_Latn",
        beam_size: int = 1,
        intra_threads: int = 2,
        cache_path: str | None = None,
    ):
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

        # NLLB-style models need the target language as a decoder prefix;
        # single-direction Marian models must not get one.
        tl_id = self.tokenizer.convert_tokens_to_ids(target_lang)
        unk = getattr(self.tokenizer, "unk_token_id", None)
        self.target_prefix = (
            [target_lang] if (tl_id is not None and tl_id != unk) else None
        )

        self.cache_path = cache_path
        self.cache: dict[str, str] = {}
        self._dirty = 0
        self._lock = threading.Lock()
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

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
        out = _cleanup(text, out)

        if use_cache and out:
            with self._lock:
                self.cache[text] = out
                self._dirty += 1
                if self.cache_path and self._dirty >= 20:
                    self._save_locked()
        return out

    def _save_locked(self):
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
            self._dirty = 0
        except Exception:
            pass

    def save_cache(self):
        if self.cache_path:
            with self._lock:
                self._save_locked()


def bench(translator: Translator, samples: list[str]) -> list[tuple[str, str, float]]:
    out = []
    for s in samples:
        t0 = time.perf_counter()
        ar = translator.translate(s, use_cache=False)
        out.append((s, ar, (time.perf_counter() - t0) * 1000))
    return out
