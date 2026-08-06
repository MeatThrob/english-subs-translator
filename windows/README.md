# darija-subs — Windows

> **Status: EXPERIMENTAL — never executed on Windows. Testers wanted.**
>
> Since v1.2.0 the Windows build is **feature-identical to macOS**: the
> same shared code translates into **any of 202 languages** (it is no
> longer a Darija-only fork). Everything platform-independent — the
> capture→OCR→translate→overlay pipeline, the 202-language registry and
> per-language model routing, the per-(model, language) caches, the Qt
> GUI **including the searchable language picker and live language
> switching**, the Qt overlay's RTL handling, the Qt region picker —
> has been verified end-to-end **on macOS running this exact Qt/mss code
> path** (see "What is verified" below). But the Windows-specific
> subsystems have **never run on a real Windows machine**:
>
> - the WinRT OCR backend (`Windows.Media.Ocr`),
> - capture-exclusion (`SetWindowDisplayAffinity` /
>   `WDA_EXCLUDEFROMCAPTURE`),
> - the DPI logical→physical pixel conversion on scaled displays,
> - the Windows font cascade (Yu Gothic / Malgun Gothic / Nirmala UI /
>   ... resolve only on real Windows),
> - `setup.bat` / `run.bat` / `gui.bat` themselves.
>
> **[`FINISHING.md`](FINISHING.md) is the handoff document**: it walks
> the first person on real Windows through verifying each of these in
> order, with expected outputs, failure modes, and what to report back.

Live English-subtitle → **any of 202 languages** overlay (Darija is the
default and has its own specialist model). Works over any app or
website showing English subs (Crunchyroll, VLC, browser, anything)
because it reads screen pixels. 100% local — no internet after setup.

## How this relates to the macOS version

Same program. Since v1.2.0 there is one `darija_subs` package at the
repo root: all logic (translation, language registry, caching, subtitle
filtering, the capture→OCR→translate→overlay loop) is shared, and only
`darija_subs/platform/win/` vs `platform/mac/` differ (capture, OCR,
overlay, picker, GUI toolkit). A fix on one platform is a fix on both.

## Install (one time)

1. Install **Python 3.12** from python.org
   (check *"Add python.exe to PATH"* during install). 3.12 specifically:
   the Windows OCR bindings (`winsdk`) do not ship wheels for newer
   Pythons.
2. Run **`windows\setup.bat`** (from the repo root or by
   double-clicking). It creates a virtualenv at the repo root, installs
   dependencies, then downloads **both** translation models from Hugging
   Face and converts them to int8:
   - Terjman-Nano-v2.1 (~300 MB download, 80 MB on disk) — serves Darija;
   - facebook/nllb-200-distilled-600M (~2.5 GB download, 635 MB on
     disk) — serves the other 201 languages.
   The weights are not in this repo — they carry their own
   non-commercial license terms, see the root README's Licensing
   section.

## Verify on this machine (do this before trusting it)

Run the self-test sequence in [`FINISHING.md`](FINISHING.md) — it is
ordered so each test only depends on the ones before it, tells you what
a pass looks like, and what to do on a fail. The short version:

```bat
.venv\Scripts\python windows\tests\bench_translate.py
.venv\Scripts\python windows\tests\bench_ocr.py
.venv\Scripts\python windows\tests\test_downscale.py
.venv\Scripts\python windows\tests\test_win_fonts.py
.venv\Scripts\python windows\tests\test_exclusion.py
.venv\Scripts\python windows\tests\e2e_test.py
```

## Run

**The app window** (easiest): double-click **`windows\gui.bat`**.

A small panel opens with the current state on one big button — red
**Off** when stopped, green **On** while translating. Press it to
switch. The **Language** box searches all 202 languages as you type —
partial, case-insensitive, English or native spelling all work ("spa",
"esp", "русский", "DARIJA") — with your recently-used languages at the
top of the dropdown. Switching while running restarts the overlay in
the new language automatically. There is also a text-size slider and a
colour wheel with a hex bar. Closing the window quits. Settings
(language, size, colour, region) are remembered in `config.json`.

**Command line**: `windows\run.bat` starts it, Ctrl+C stops it.

```bat
windows\run.bat --pick             (first run: drag-select the subtitle area)
windows\run.bat                    (reuse saved region + language)
windows\run.bat --lang Spanish     (by name...)
windows\run.bat --lang jpn_Jpan    (...or NLLB code)
windows\run.bat --list-langs       (print all 202)
```

Region precedence is the same as macOS: `--region` > `--pick` >
explicit `--band` > the saved/picked region in `config.json` >
lower-half auto-scan. A saved region that no longer overlaps any
display (monitor unplugged) is ignored rather than captured blind.

Useful options (full list: `windows\run.bat --help`):

| flag | meaning |
|------|---------|
| `--lang NAME\|CODE` | target language (name, native name, or NLLB code) |
| `--position above\|below\|cover` | where the translation goes; `cover` hides the English under a dark box (run `test_exclusion.py` first — see FINISHING.md) |
| `--font-size N` | subtitle size (default 42) |
| `--ocr winrt\|rapid` | OCR engine (default: Windows built-in) |
| `--beam N` | beam size (default 2; `--beam 1` ≈ halves NLLB latency) |
| `--turbo` | lowest latency: near-continuous OCR + hide on first empty read. More CPU |

## What is verified, honestly

Everything below ran on macOS against **this exact code** (the Qt/mss
platform stack, forced via `DARIJA_SUBS_PLATFORM=win`), using the same
Python version (3.12) the Windows setup installs:

- Darija output character-identical to the macOS version's 46-line
  golden baseline, through this tree's translate path.
- The full pipeline e2e (Qt fake player) passes in **Darija** and in
  **Spanish** (NLLB routing), including the overlay-feedback flap guard.
- The Qt control panel self-test passes end-to-end, **including typing
  partial/native/case-insensitive names into the language picker,
  switching languages while the overlay is live (auto stop → model swap
  → auto restart), recents ordering, and config persistence**.
- The WinRT `MaxImageDimension` downscale guard (unit test) and the
  font cascade / RTL direction / no-tofu rendering test pass.

What that does **not** prove: WinRT OCR quality, the capture-exclusion
syscall, real DPI scaling, the `.bat` scripts, and whether the named
Windows fonts resolve. Those need a real Windows machine —
**[`FINISHING.md`](FINISHING.md)** exists precisely for that, and a
result report (pass or fail) in a GitHub issue is genuinely useful.

## Notes

- Needs Windows 10 2004+ for capture-exclusion (cover mode); Windows 11
  is fine. No special permissions needed (unlike macOS).
- Exclusive-fullscreen games can swallow topmost overlays — use the
  player's *windowed/borderless* fullscreen, which is what browsers and
  video players use anyway.
- Every translated line is cached per (model, language) under `cache\`
  — rewatching an episode gets instant translations, and a language
  switch can never replay a line from the wrong language. A
  pre-multilingual `cache.json` is migrated into the Darija shard
  automatically on first run.
- Bigger/better Darija models (gated on HuggingFace): make a free HF
  account, accept terms for `atlasia/Terjman-Large-v2.0`, then
  `ct2-transformers-converter --model atlasia/Terjman-Large-v2.0
  --output_dir models\terjman-large-int8 --quantization int8`
  and run with `--model models\terjman-large-int8`. Same non-commercial
  license caveat as the default model.
