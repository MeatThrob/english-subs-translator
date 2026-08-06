# darija-subs

Live English-subtitle → **any of 202 languages** overlay for macOS.
The name is the origin story: it began as a Moroccan Arabic (Darija)
tool, and Darija is still the default target with its own specialist
model — but it now translates into any NLLB-200 language. Point it at
any app or website showing English subs (Crunchyroll, VLC, IINA, a
browser, anything) — the translation appears in lockstep with the
English and disappears with it. 100% local: no network at runtime.

## Languages: what is and is not covered

The language list is **NLLB-200's 202 languages** (`run.sh
--list-langs`), not "everything on Google Translate". The two sets
overlap heavily but neither contains the other:

- NLLB has languages Google Translate lacks (Modern Standard Arabic
  *plus* seven regional Arabics including Darija and Egyptian, Friulian,
  Ligurian, Bemba, ...).
- Google Translate has languages NLLB-200 lacks (e.g. Cantonese is
  present, but not some of Google's long tail like Hmong or Klingon-ish
  novelties).

All major languages are covered: Spanish, French, German, Portuguese,
Italian, Russian, Ukrainian, Arabic (MSA + dialects), Hebrew, Persian,
Turkish, Hindi, Bengali, Urdu, Tamil, Japanese, Korean, Chinese
(Simplified/Traditional), Cantonese, Vietnamese, Thai, Indonesian,
Swahili, and 170+ more, each labelled with its native name in the picker.

## How it works

1. **Capture** — grabs the screen region you selected (~10x/sec, Quartz).
   Works over any player because it reads pixels, not the app.
2. **OCR** — Apple Vision `accurate` mode (benchmarked 10/10 exact on
   outlined subtitle text, ~85 ms on M2; `fast` mode was garbage: 2/10).
3. **Translate** — per-language model routing via CTranslate2 int8:
   - **Darija** → Terjman-Nano-v2.1 (77M, Darija-specialized, AtlasIA
     lineage): ~45–130 ms/line.
   - **Everything else** → facebook/nllb-200-distilled-600M: one model,
     201 more languages, ~250–500 ms/line (beam 2). Switching between
     NLLB languages retargets the loaded model in place — no reload.
   Every line is cached per language under `cache/`, so repeats and
   rewatches are instant (~0 ms).
4. **Overlay** — borderless, click-through, always-on-top AppKit window.
   Direction and font follow the target script: RTL + SF Arabic for
   Arabic-script targets, RTL for Hebrew, the system font cascade for
   Latin/Cyrillic/CJK/Indic. Excluded from screen capture
   (`NSWindowSharingNone`), so the OCR can never read its own output —
   re-verified 2026-08: without the exclusion, cover mode feeds back at
   ~13 show/hide flaps per line; with it, exactly one show per line.
   The e2e test now fails on any such flap storm.

Measured on an M2 (busy machine, load ~9; idle numbers are better),
warm per-line translation latency on real subtitle lines, beam 2:

| target | model | median | notes |
|--------|-------|--------|-------|
| Darija | terjman-nano | 56 ms | specialist: faster *and* better Darija |
| Darija | nllb-600m | 1223 ms | why the specialist stays: 22x slower, mistranslations |
| Arabic (MSA) | nllb-600m | 418 ms | |
| Spanish | nllb-600m | 465 ms | |
| Japanese | nllb-600m | 361 ms | |
| Russian | nllb-600m | 420 ms | |

`--beam 1` roughly halves NLLB latency (Spanish 359 → Japanese 192 ms
median measured) at a small quality cost — worth it if you want minimum
lag on an NLLB language. End-to-end (fake player, Darija): show avg
~190–300 ms, hide ~100–130 ms, cache hits ~90 ms. Spanish/NLLB:
show avg ~735 ms.

## Usage

**Control panel** (recommended — has the language picker):

```bash
./gui.sh          # from the repo directory
```

Type in the Language box to search all 202 — partial, case-insensitive,
English or native spelling all work ("spa", "esp", "русский", "DARIJA").
Recently-used languages sit at the top of the dropdown, current first.
The choice is saved and reused; switching while running restarts the
overlay in the new language automatically (~0.3 s between already-loaded
models).

**CLI:**

```bash
./run.sh                  # last-used language (Darija by default)
./run.sh --lang Spanish   # by name…
./run.sh --lang jpn_Jpan  # …or NLLB code
./run.sh --list-langs     # the full list
pkill -f darija_subs.main # turn it off
```

`--lang`, `--position`, `--font-size` and the capture region are
remembered: whatever you pass (or pick) becomes the default for the
next plain `./run.sh`.

(Ctrl+C also stops it. `./run.sh --pick` hand-selects a region.)

**Which part of the screen is scanned.** A hand-picked box
(`./run.sh --pick`) is saved to `config.json` and reused by both
`run.sh` and the control panel until you pick again. Without one, both
scan the lower half of the main display. An explicit `--region` or
`--band` overrides the saved box; a saved box that no longer overlaps
any display (monitor unplugged) is ignored rather than captured blind.
Snugger is better: fewer pixels to OCR and fewer chances to pick up
player UI.

Options:

| flag | meaning |
|------|---------|
| `--lang NAME\|CODE` | target language (name, native name, or NLLB code) |
| `--position above\|below\|cover` | where the translation goes. `cover` hides the English under a dark box |
| `--font-size N` | subtitle size (default 46) |
| `--interval MS` | poll rate, default 50 |
| `--hide-frames N` | empty reads before hiding (default 2 ≈ 200 ms) |
| `--turbo` | lowest latency: near-continuous OCR + hide on first empty read. Costs more CPU |
| `--beam N` | beam size (default 2; `--beam 1` ≈ halves NLLB latency) |
| `--model DIR` | force a specific model dir (overrides per-language routing) |
| `--headless --log f.jsonl` | no overlay; just log events (testing) |

## Setup from a clean clone

Models and the venv are **not** in the repo (≈715 MB, rebuildable):

```bash
git clone https://github.com/MeatThrob/darija-subs.git
cd darija-subs
python3 -m venv .venv                     # Python 3.10+ (3.12 and 3.14 tested)
.venv/bin/pip install ctranslate2 transformers torch pillow \
                      pyobjc numpy sentencepiece
tools/setup_model.sh   # downloads + converts both models to int8
```

`torch` is needed only at setup time — `ct2-transformers-converter` loads
the Hugging Face checkpoints through it before quantizing. `pillow` is
needed only by `tests/bench_ocr.py`. Neither is imported at runtime.

Requirements:

- macOS on Apple Silicon (Vision OCR + AppKit; developed and measured on
  an M2).
- Screen Recording permission for your terminal
  (System Settings → Privacy & Security → Screen Recording). The app
  checks this at startup and tells you if it is missing. No Accessibility
  permission is needed — the overlay never reads or synthesizes input.
- The subtitle region should only contain the subtitles (drag a snug
  box). Text >220 chars is ignored as a safety valve.

## Models on disk

- `models/terjman-nano-int8` — 80 MB, serves **Darija** (default).
  Genuinely Moroccan output (`abdeljalilELmajjodi/Terjman-Nano-v2.1`).
- `models/nllb-600m-int8` — 635 MB, serves **the other 201 languages**
  (`facebook/nllb-200-distilled-600M`, int8).

Routing lives in `darija_subs/langs.py` (`SPECIALIST_MODELS`); drop in
another specialist (e.g. a gated Terjman-Large after `huggingface-cli
login`) and map its language there.

## Other platforms

**Windows — experimental, in [`windows/`](windows/).** A Qt/PySide6 port
(WinRT OCR, `mss` capture, `WDA_EXCLUDEFROMCAPTURE` for
capture-exclusion). Its platform-independent parts — pipeline, GUI,
translation — are verified end-to-end on macOS (e2e PASS, GUI self-test
PASS, Darija output char-identical to this repo's 46-line golden), but
**it has never been executed on a real Windows machine**: the WinRT OCR
backend, the capture-exclusion call, the DPI scaling and the `.bat`
scripts are all unexercised. It is also **Darija-only** — it predates
the multilingual work and does not yet have the 202-language support
above. `windows/README.md` states exactly what is unverified and ships
self-tests so the first Windows user can find out in ~2 minutes;
results reported in an issue are genuinely welcome.

**Linux — not supported yet.** No port exists, and honesty about the
delta: OCR would be easy (the Windows port's RapidOCR backend is
cross-platform), but under Wayland three things this app depends on are
restricted or impossible — `mss` cannot capture other windows (needs
xdg-desktop-portal/PipeWire), a client cannot place an always-on-top
overlay at absolute screen coordinates (needs layer-shell, which GNOME
does not support), and there is no capture-exclusion equivalent at all.
An X11-only port would be a modest job; a Wayland one is a real project.
Neither exists today.

## Licensing

**The MIT license in this repo covers the code only — not the model
weights.** No weights are redistributed here; `tools/setup_model.sh`
(macOS) and `windows/setup.bat` (Windows) download them from Hugging
Face onto your machine at setup time, and what you may do with them is
governed by their own licenses:

- `facebook/nllb-200-distilled-600M` — **CC-BY-NC-4.0
  (non-commercial)**.
- `abdeljalilELmajjodi/Terjman-Nano-v2.1` — no license declared on its
  model card; its parent, `atlasia/Terjman-Nano-v2.0`, is
  **CC-BY-NC-4.0 (non-commercial)**, so treat this one as
  non-commercial too.

Practical upshot: as configured, use this tool for **personal /
non-commercial** purposes. If you need commercial use, swap in models
whose licenses permit it (the code doesn't care — any CTranslate2
Marian/NLLB-style model works).

## Caches

You do not need to create `config.json` — with no config the app scans
the lower half of the main display and writes the file itself on first
run. `config.example.json` just shows the shape and the accepted keys;
its `region` is one machine's numbers, not a default to copy.

`cache/<model>__<lang>.json` — one shard per (model, language) pair, so
a language switch can never replay a line from the wrong language. A
pre-multilingual `cache.json` is migrated into the Darija shard
automatically on first run.

## Tests

```bash
.venv/bin/python tests/bench_ocr.py           # OCR accuracy/latency on synthetic frames
.venv/bin/python tests/test_multilang_unit.py # cache sharding, retarget, cleanup, migration
.venv/bin/python tests/regress_darija.py      # Darija golden outputs (46 lines, char-exact)
.venv/bin/python tests/bench_translate.py     # per-language latency table (above)
.venv/bin/python tests/e2e_test.py            # full pipeline vs. a fake subtitle player
E2E_EXTRA="--lang Spanish" .venv/bin/python tests/e2e_test.py   # e2e in another language
.venv/bin/python -m darija_subs.gui --self-test  # control panel incl. language switching
```

`tests/e2e_test.py` and the GUI `--self-test` both restore your real
`config.json` afterwards (they drive handlers that save to it on
purpose). The e2e also fails on spurious show/hide storms — the
overlay-feedback signature; removing `NSWindowSharingNone` takes it from
8 app shows to ~90 for 8 subtitle lines, and the test fails.
`tests/regress_darija.py` pins Darija output to the pre-multilingual
baseline; run it after touching anything in the translation path.
