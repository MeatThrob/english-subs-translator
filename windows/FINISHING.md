# Finishing the Windows port — a handoff for the first person on real Windows

Nobody has ever run this program on a real Windows machine. Everything
platform-independent is verified on macOS **against this exact code**
(the same Qt/mss stack, forced with `DARIJA_SUBS_PLATFORM=win`), so the
open questions are narrow and specific. This document is the worklist:
what each unverified subsystem is supposed to do, how to tell whether it
works, what failure looks like from the chair (not the stack trace), and
what to report so it can be fixed remotely.

Work through it top to bottom — each step only depends on the ones
before it. Total time if everything passes: ~20 minutes plus the model
downloads.

---

## 0. What is already known-good (don't re-debug these)

Verified end-to-end on macOS running this tree's Windows/Qt code path,
on the same Python (3.12) that setup.bat installs:

| verified | how |
|---|---|
| Translation engine + Darija quality | 46-line golden test, char-identical to the macOS daily driver |
| 202-language registry, model routing, per-language caches | unit tests + live GUI switching |
| Capture→OCR→translate→overlay loop | e2e vs a scripted fake player, in Darija **and** Spanish, incl. the anti-feedback flap guard |
| Qt control panel incl. the language picker | scripted self-test: partial/native/case-insensitive search, live language switch with auto-restart, recents, config persistence |
| Qt overlay RTL + font-cascade mechanism | test_win_fonts.py (the *mechanism*; the Windows font *names* resolve only on Windows — step 5) |
| WinRT image-size downscale guard | unit test on synthetic 3840px frames (never against real WinRT — step 3) |

If one of the steps below fails, the cause is almost certainly in the
step's own subsystem, not in the shared logic above.

## 1. `setup.bat` — never executed

**What it should do:** create `.venv\` at the repo root, install
`windows\requirements.txt`, then download and int8-convert **both**
models into `models\terjman-nano-int8` and `models\nllb-600m-int8`.
Re-running it must be a no-op for anything already present.

**Pass looks like:** "Setup complete." and both `models\...\model.bin`
files existing (~80 MB and ~635 MB).

**Failure from the chair:**
- *"Python launcher not found"* → install Python 3.12 from python.org
  with "Add python.exe to PATH" ticked.
- *pip fails mentioning `winsdk`* → your Python is newer than 3.12.
  `winsdk` ships wheels for 3.10–3.12 only. Install 3.12; setup.bat
  prefers it automatically.
- *conversion dies mid-download* → network hiccup; re-run, it resumes.
- Anything else: report the last ~20 lines of output verbatim.

## 2. Translation sanity — `windows\tests\bench_translate.py`

**Pass looks like:** 5 Darija lines, each roughly 40–200 ms. This is
the same engine the macOS version uses daily; a failure here means the
*environment* (venv, model files), not the code.

## 3. WinRT OCR — `windows\tests\bench_ocr.py` — the big unknown

**What it is:** `Windows.Media.Ocr`, the OCR built into Windows 10/11,
reading synthetic anime-style subtitle frames (white text, black
outline, noisy background).

**Pass looks like:** `WinRTBackend: 8/10 or better exact, avg < ~300 ms`.
(For reference the macOS Vision backend scores 10/10 at ~80 ms.)

**Failure from the chair:**
- *"No OCR language available"* → Settings → Time & Language →
  Language & region → add **English (United States)**.
- *Score below 8/10* → WinRT quality on stylized text is the port's
  known biggest risk. Install the fallback engine and compare:
  `.venv\Scripts\pip install rapidocr-onnxruntime`, then
  `.venv\Scripts\python windows\tests\bench_ocr.py rapid`. If rapid
  scores higher, use the app with `--ocr rapid` and report both scores.
- *Crash inside winsdk* → report the traceback + your Windows version;
  the winsdk↔modular-winrt import fallback in
  `darija_subs/platform/win/ocr.py` may need adjusting for your build.

**Also in this area:** the `MaxImageDimension` guard. WinRT rejects
images over ~2600 px outright, and on any scaled display the default
lower-half region exceeds that in physical pixels, so `recognize()`
downscales first. The maths is unit-tested
(`windows\tests\test_downscale.py` — run it, expect PASS anywhere) but
it has **never met real WinRT**. The symptom of it being wrong:
`ocr_error` events on *every* frame with a huge capture region, but OCR
fine with a small `--pick`ed one. Report `MaxImageDimension` as printed
by: `.venv\Scripts\python -c "from winsdk.windows.media.ocr import OcrEngine; print(OcrEngine.max_image_dimension)"`

## 4. DPI / display scaling — the second big unknown

**What it is:** Windows displays commonly run at 125–200 % scaling. The
region is stored in *logical* coordinates plus the screen's
devicePixelRatio; `mss` wants *physical* pixels on Windows, so
`darija_subs/platform/win/capture.py` multiplies by the dpr. Nobody has
confirmed Qt's dpr and mss's pixel origin agree on real hardware,
especially multi-monitor.

**How to check:** run `windows\run.bat --pick`, drag a box tightly
around a subtitle, and confirm the translation appears **directly above
that box** — not offset, not half-size, not on the wrong monitor.

**Failure from the chair:** overlay appears displaced or scaled ≈1.25×/
1.5×/2× away from where you picked → dpr conversion is wrong for your
setup. Report: display resolution(s), scaling %(s), monitor arrangement,
and the `region`/`dpr` values from `config.json`.

## 5. Fonts — `windows\tests\test_win_fonts.py`

**What it is:** the overlay picks a per-script Windows font cascade
(Yu Gothic for Japanese, Malgun Gothic for Korean, Microsoft
YaHei/JhengHei for Chinese, Nirmala UI for Indic, Leelawadee UI for
Thai/Lao/Khmer, Myanmar Text, Ebrima for Ethiopic/Tifinagh, Microsoft
Himalaya for Tibetan, Segoe UI for everything else). The test renders a
sample of every script and fails if it comes out as .notdef boxes
(tofu). It passes on macOS via macOS's own fallback fonts — **the run
on real Windows is the one that proves the named Windows fonts
resolve**.

**Pass looks like:** `PASS` with every `glyphs resolve` line ok.

**Failure from the chair:** a language renders as hollow boxes in the
overlay → that script's font is missing on your Windows edition (N
editions strip some). Report which script failed; the fix is adding an
installed family to `_SCRIPT_FONTS` in
`darija_subs/platform/win/overlay.py`.

## 6. Capture-exclusion — `windows\tests\test_exclusion.py`

**What it is:** `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`
makes the overlay invisible to screen capture so OCR can never read its
own output back (the feedback loop shows up as rapid show/hide
flapping). Needs Windows 10 2004+.

**Verdicts:**
- `SEE-THROUGH` → best case: `--position cover` is safe.
- `BLACKED-OUT` → exclusion works but capture sees black; keep the
  default `above` position (correct regardless).
- `VISIBLE` → exclusion failed (old Windows 10). Do NOT use `cover`.
  `above`/`below` remain safe: the overlay does not overlap the
  captured region, and two software guards back it up (Arabic-script
  reads are discarded; for Latin-script targets the >220-char and
  subtitle-shape filters catch most feedback). If you see rhythmic
  flapping in any position, that is the feedback signature — report it
  with your Windows version.

## 7. The full pipeline — `windows\tests\e2e_test.py`

**What it does:** opens a scripted fake "player" window that cycles 8
subtitle lines, runs the real app against it, and matches the two event
timelines. Also fails on spurious show/hide storms (the feedback
signature) and restores your `config.json` afterwards.

**Pass looks like:** `PASS`, show latency well under a second (Darija).

Then repeat in an NLLB language to prove the multilingual path on this
machine:

```bat
set E2E_EXTRA=--lang Spanish
.venv\Scripts\python windows\tests\e2e_test.py
```

**Known sensitivity:** the e2e asserts wall-clock latency (show ≤ 2 s,
hide ≤ 1.2 s). NLLB at beam 2 costs ~250–500 ms/line on an idle M2 and
more on a busy or slow machine — on a heavily loaded host this test can
fail on *latency alone* while every translation is correct (it does
exactly that on a loaded Mac). If you see only `MISS`es with correct
text and no `SPURIOUS` line, close background load or use
`set E2E_EXTRA=--lang Spanish --beam 1` and re-run before concluding
anything is broken.

## 8. The GUI — `gui.bat` and its self-test

```bat
.venv\Scripts\python -m darija_subs.gui --self-test
```

**Pass looks like:** the panel opens, drives itself (On/Off, colours,
typing "spa" / "darija" / "русский" into the language box, switching
mid-run), prints `PASS`, exits, and leaves `config.json` byte-identical.
Then use `windows\gui.bat` like a human: pick a language, press On over
a paused video with subtitles.

## 9. What to report back

Open a GitHub issue with:

- Windows version + build (`winver`), display resolution(s) and
  scaling %, single/multi monitor, GPU.
- Which numbered step you were on, the exact command, and its full
  output (copy the console).
- For OCR issues: the two bench scores (winrt vs rapid).
- For overlay-position issues: `config.json`'s `region` and `dpr`.
- The pass/fail verdict of every step you ran, including the passes —
  "steps 1–5 pass, 6 says VISIBLE" is a complete, actionable report.

## 10. Genuinely unfinished vs. merely unverified

Honesty about the difference:

**Believed working, never executed on Windows** (everything above):
WinRT OCR, capture-exclusion, DPI conversion, the .bat scripts, the
font names. The code paths exist, are complete, and their
platform-independent halves pass tests; what is missing is one run on
real hardware.

**Known gaps (nobody wrote the code):**
- No Windows CI — verification is manual, hence this document.
- The CLI's saved-region "monitor unplugged" guard runs in the GUI
  flows but not in `--headless` mode on Windows (Qt is not up yet
  there; macOS checks it via Quartz in all modes). Consequence: a
  headless run against an unplugged monitor's region captures nothing
  and logs `capture_failed` instead of falling back.
- `--fast-ocr` is a macOS/Vision knob; it is accepted and ignored on
  Windows.
- `--position cover` on machines where exclusion reports VISIBLE has no
  hardware mitigation (software guards only, see step 6).
- Packaging (an installer / single .exe) does not exist; the venv +
  .bat flow is the supported path.
