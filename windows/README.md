# darija-subs — Windows port

> **Status: EXPERIMENTAL — never executed on Windows. Testers wanted.**
>
> Everything platform-independent here (the capture→OCR→translate→overlay
> pipeline, the Qt GUI, the translation itself) has been verified
> end-to-end **on macOS**: the e2e test passes, the GUI self-test passes,
> and the Darija output is character-identical to the macOS version's
> 46-line golden baseline. But the Windows-specific subsystems have
> **never run on a real Windows machine**:
>
> - the WinRT OCR backend (`Windows.Media.Ocr`),
> - capture-exclusion (`SetWindowDisplayAffinity` /
>   `WDA_EXCLUDEFROMCAPTURE`),
> - the DPI logical→physical pixel conversion on scaled displays,
> - `setup.bat` / `run.bat` / `gui.bat` themselves.
>
> The self-tests below exist precisely so the **first person on real
> Windows** can verify these in ~2 minutes. If you run them, please
> report results (pass or fail) in a GitHub issue — that is genuinely
> useful.

Live English-subtitle → **Moroccan Arabic (Darija)** overlay.
Works over any app or website showing English subs (Crunchyroll, VLC,
browser, anything) because it reads screen pixels. 100% local — no
internet needed after setup.

**Darija-only for now.** The macOS version at the repo root translates
into any of 202 languages; this port predates that work and supports
only English → Darija. Multilingual parity is planned (the language
registry and per-language caches are platform-independent).

## Install (one time)

1. Install **Python 3.12** from python.org
   (check *"Add python.exe to PATH"* during install). 3.12 specifically:
   the Windows OCR bindings (`winsdk`) do not ship wheels for newer
   Pythons.
2. Double-click **`setup.bat`** (or run it in a terminal). It creates a
   virtualenv, installs the dependencies, then downloads
   Terjman-Nano-v2.1 from Hugging Face and converts it to int8
   (~300 MB download once, ~80 MB on disk). The weights are not in this
   repo — they carry their own non-commercial license terms, see the
   root README's Licensing section.

## Verify on this machine (do this before trusting it)

```bat
.venv\Scripts\python tests\bench_translate.py
```
Expect 5 Darija lines at roughly 40–200 ms each. This part is
well-tested (it is the same engine the macOS version uses daily).

```bat
.venv\Scripts\python tests\bench_ocr.py
```
**Unverified on Windows.** Expect `WinRTBackend: 8/10 or better`. If it
scores lower, run `.venv\Scripts\pip install rapidocr-onnxruntime`, then
`.venv\Scripts\python tests\bench_ocr.py rapid` and use the app with
`--ocr rapid` if it scores higher. If Windows OCR says no language is
available: Settings → Time & Language → Language & region → add
**English (United States)**.

```bat
.venv\Scripts\python tests\test_exclusion.py
```
**Unverified on Windows.** Says whether `--position cover` is safe
(SEE-THROUGH) or whether you should stick with the default `above`
position (BLACKED-OUT/VISIBLE). The default `above`/`below` positions
are safe regardless — the Arabic never overlaps the captured region,
and OCR'd Arabic is discarded as a second line of defence.

```bat
.venv\Scripts\python tests\e2e_test.py
```
Full pipeline test with a fake subtitle player. Expect `PASS` with show
latency well under a second. It also fails on show/hide storms (the
overlay-feedback signature) and restores your `config.json` afterwards.

## Run

**The app window** (easiest — one button): double-click **`gui.bat`**.

A small panel opens: the button shows the current state — red **Off**
when stopped, green **On** while it is translating. Press it to switch.
It also has a text-size slider and a colour wheel with a hex bar for the
Arabic subtitles. Closing the window quits. Settings are remembered.

**Command line**, if you prefer it: `run.bat` starts it, Ctrl+C (or
closing the window) stops it. `run.bat --pick` drag-selects the subtitle
region; the picked box is saved and reused.

Either way, with no picked region it scans the lower half of the screen
and mirrors any white subtitle lettering it finds, with or without a
background box. Banners and UI text are filtered out by size, colour,
position and clustering.

Useful options:

| flag | meaning |
|------|---------|
| `--position above\|below\|cover` | where the Arabic goes; `cover` hides the English under a dark box (run `tests\test_exclusion.py` first — see above) |
| `--font-size N` | Arabic text size (default 42) |
| `--ocr winrt\|rapid` | OCR engine (default: Windows built-in) |
| `--interval MS` | poll rate (default 50) |
| `--turbo` | lowest latency: near-continuous OCR + hide on first empty read. More CPU; rare flicker on washed-out scenes |

## If OCR fails

The most likely failure mode on first run is OCR, since the WinRT
backend is the untested part:

- `ocr_error` events or "OCR error" in the panel → try
  `.venv\Scripts\pip install rapidocr-onnxruntime` and run with
  `--ocr rapid` (cross-platform ONNX models, slower but proven).
- "No OCR language available" → install **English (United States)** in
  Settings → Time & Language → Language & region.
- Oversized-region errors should not occur — frames larger than WinRT's
  `MaxImageDimension` (~2600 px) are downscaled automatically before
  recognition — but this downscale itself has only been unit-tested, not
  exercised against real WinRT.

## Notes

- Needs Windows 10 2004+ for capture-exclusion (cover mode); Windows 11
  is fine. No special permissions needed (unlike macOS).
- Exclusive-fullscreen games can swallow topmost overlays — use the
  player's *windowed/borderless* fullscreen, which is what browsers and
  video players use anyway.
- Every translated line is cached in `cache.json` — rewatching an
  episode gets instant translations.
- Bigger/better Darija models (gated on HuggingFace): make a free HF
  account, accept terms for `atlasia/Terjman-Large-v2.0`, then
  `ct2-transformers-converter --model atlasia/Terjman-Large-v2.0
  --output_dir models\terjman-large-int8 --quantization int8`
  and run with `--model models\terjman-large-int8`. Same non-commercial
  license caveat as the default model.
