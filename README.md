# darija-subs

Live English-subtitle → **Moroccan Arabic (Darija)** overlay for macOS.
Point it at any app or website showing English subs (Crunchyroll, VLC,
IINA, a browser, anything) — the Arabic appears in lockstep with the
English and disappears with it. 100% local: no network at runtime.

## How it works

1. **Capture** — grabs the screen region you selected (~10x/sec, Quartz).
   Works over any player because it reads pixels, not the app.
2. **OCR** — Apple Vision `accurate` mode (benchmarked 10/10 exact on
   outlined subtitle text, ~85 ms on M2; `fast` mode was garbage: 2/10).
3. **Translate** — Terjman-Nano-v2.1 (77M, Darija-specialized, AtlasIA
   lineage) via CTranslate2 int8: ~40–130 ms/line. Every line is cached
   to `cache.json`, so repeats and rewatches are instant.
4. **Overlay** — borderless, click-through, always-on-top AppKit window.
   Native Arabic shaping/RTL. Excluded from screen capture
   (`NSWindowSharingNone`), so the OCR can never read its own output —
   verified: in `cover` mode OCR reads the English *through* the overlay.

Defaults are the **accuracy-first** configuration: Vision `accurate`
OCR, beam=2 translation (measured best quality on this model), 2-frame
hide confirmation, 50 ms polling. Measured end-to-end (fake player):
steady-state show ~160-240 ms, hide ~110-130 ms, cache hits ~90-130 ms.
8/8 lines matched, 8/8 hides mirrored.

## Usage

**Turn it on** (no setup — auto-scans the lower half of the screen):

```bash
~/Developer/darija-subs/run.sh
```

**Turn it off:**

```bash
pkill -f darija_subs.main
```

(Ctrl+C also stops it if it's running in your terminal window.
`./run.sh --pick` exists if you ever want to hand-select a region.)

Options:

| flag | meaning |
|------|---------|
| `--position above\|below\|cover` | where the Arabic goes. `cover` hides the English under a dark box and puts the Arabic on top |
| `--font-size N` | Arabic size (default 34) |
| `--interval MS` | poll rate, default 50 |
| `--hide-frames N` | empty reads before hiding (default 2 ≈ 200 ms) |
| `--turbo` | lowest latency: near-continuous OCR + hide on first empty read. Measured: hide avg 51 ms, new-line show ~160–190 ms, cached ~90 ms. Costs more CPU; rare flicker possible on washed-out scenes |
| `--model DIR` | swap translation model (see below) |
| `--headless --log f.jsonl` | no overlay; just log events (testing) |

## Requirements

- Screen Recording permission for your terminal
  (System Settings → Privacy & Security → Screen Recording). Already
  granted on this machine.
- The subtitle region should only contain the subtitles (drag a snug
  box). Text >220 chars is ignored as a safety valve.

## Models on disk

- `models/terjman-nano-int8` — **default.** 80 MB, best speed, genuinely
  Moroccan output (`abdeljalilELmajjodi/Terjman-Nano-v2.1`).
- `models/nllb-600m-int8` — fallback, broader domain coverage but ~20x
  slower on this machine and MSA-leaning diacritized output
  (`facebook/nllb-200-distilled-600M`, target `ary_Arab`).

Rebuild either with `tools/setup_model.sh`.

**Upgrade path:** the larger Darija models (`atlasia/Terjman-Large-v2.0`,
`Terjman-Ultra`) are gated on HuggingFace — make a free HF account,
accept the terms, `huggingface-cli login`, then convert with
`ct2-transformers-converter --model atlasia/Terjman-Large-v2.0
--output_dir models/terjman-large-int8 --quantization int8` and run with
`--model models/terjman-large-int8`. Same code path (NLLB-style models
are auto-detected).

## Tests

```bash
.venv/bin/python tests/bench_ocr.py   # OCR accuracy/latency on synthetic frames
.venv/bin/python tests/e2e_test.py    # full pipeline vs. a fake subtitle player
```
